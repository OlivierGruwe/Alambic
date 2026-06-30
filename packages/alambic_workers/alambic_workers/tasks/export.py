"""alambic_workers.tasks.export — export d'un document validé.

Déclenchée après la validation humaine (statut VALIDATED). Récupère le PDF du
document depuis Garage, résout la configuration d'export (web service ou S3
sortant), effectue l'export, et ne fait passer le document à EXPORTED qu'APRÈS
confirmation de l'upload. En cas d'échec, le document repasse à VALIDATED (il
reste exportable) et l'erreur est journalisée.

Idempotence : un document déjà EXPORTED n'est pas réexporté.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from alambic_core import storage
from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentStatus
from alambic_core.models import Config, Document
from alambic_core.services.export import export_config_from_config, run_export

logger = logging.getLogger(__name__)


def _secret_provider():
    """Provider de déchiffrement (registre global), ou None si indisponible."""
    try:
        from alambic_core.db.types import get_secret_provider

        return get_secret_provider()
    except Exception:  # noqa: BLE001
        return None


def export_document(doc_id: str) -> dict:
    """Exporte un document validé. Renvoie {ok, status, error?}.

    Transition : VALIDATED → (export) → EXPORTED si succès, retour VALIDATED sinon.
    EXPORTED est posé seulement après confirmation de l'upload.
    """
    # 1) Vérifier l'état + récupérer ce qu'il faut (PDF location, config).
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return {"ok": False, "error": "document_introuvable"}

        if doc.status == DocumentStatus.EXPORTED.value:
            return {"ok": True, "status": "EXPORTED", "skipped": True}

        if doc.status != DocumentStatus.VALIDATED.value:
            # On n'exporte que les documents validés.
            return {"ok": False, "error": f"statut_invalide:{doc.status}"}

        bucket = doc.bucket_name or storage.work_bucket()
        key = doc.object_key
        config_id = doc.transaction.config_id if doc.transaction is not None else None

        if not key:
            return {"ok": False, "error": "pas_de_pdf"}

        config = s.get(Config, config_id) if config_id else None
        if config is None:
            return {"ok": False, "error": "config_introuvable"}

        # Complétude du dossier : si la config l'exige et que la transaction est
        # incomplète (doctype obligatoire manquant), on bloque l'export — sauf si
        # un opérateur a forcé le déblocage (completeness_override).
        tx = doc.transaction
        if tx is not None and not tx.completeness_override:
            from alambic_core.models import Doctype
            from alambic_core.services.completeness import compute_completeness

            doctype_names = {d.id: d.doctype_name for d in s.query(Doctype).all()}
            comp = compute_completeness(config, tx.documents, doctype_names)
            if comp.enabled and not comp.complete:
                missing_names = [doctype_names.get(d, d) for d in comp.missing_required]
                logger.info(
                    "Export bloqué pour %s : dossier incomplet, manque %s",
                    doc_id,
                    missing_names,
                )
                return {
                    "ok": False,
                    "error": "dossier_incomplet",
                    "missing_required": missing_names,
                }

        # Allowlist anti-SSRF du compte (enrich_allowed_domains).
        from alambic_core.models import Account
        from alambic_core.security.url_guard import parse_allowed_domains

        account = s.get(Account, config.account_id) if config.account_id else None
        allowed_domains = parse_allowed_domains(
            account.enrich_allowed_domains if account is not None else ""
        )

        export_cfg = export_config_from_config(config, _secret_provider(), allowed_domains)

    if not export_cfg.way_out:
        logger.info("Export sauté pour %s : aucune destination configurée", doc_id)
        return {"ok": False, "error": "no_way_out"}

    # 2) Récupérer le PDF depuis Garage.
    try:
        pdf_bytes = storage.get_bytes(bucket, key)
    except Exception as ex:  # noqa: BLE001
        logger.error("Export : PDF illisible pour %s : %s", doc_id, ex)
        return {"ok": False, "error": "pdf_illisible"}

    # 3) Exporter (le document est rechargé pour construire le payload à jour).
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        # transaction_fields=None : build_payload dérive les champs hérités
        # (propagés + enrichissement WS) depuis les index metadata du document.
        result = run_export(pdf_bytes, doc, export_cfg, transaction_fields=None)

    # 4) Statut : EXPORTED seulement après confirmation.
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            return {"ok": False, "error": "document_disparu"}
        if result.ok:
            doc.status = DocumentStatus.EXPORTED.value
            logger.info("Document %s exporté (%s)", doc_id, export_cfg.way_out)

            # Si tous les documents actifs de la transaction sont exportés,
            # horodater la transaction (déclenche la rétention via exported_at).
            tx = doc.transaction
            if tx is not None and tx.exported_at is None:
                from alambic_core.services.transaction_status import active_documents

                actives = active_documents(tx.documents)
                if actives and all(
                    d.status == DocumentStatus.EXPORTED.value for d in actives
                ):
                    tx.exported_at = datetime.now(UTC)
                    logger.info("Transaction %s entièrement exportée", tx.id)
        else:
            # Échec : le document reste validé (réexportable), on ne le perd pas.
            doc.status = DocumentStatus.VALIDATED.value
            logger.warning(
                "Export %s échoué pour %s : %s", export_cfg.way_out, doc_id, result.error
            )

    return {
        "ok": result.ok,
        "status": "EXPORTED" if result.ok else "VALIDATED",
        "error": result.error or None,
    }
