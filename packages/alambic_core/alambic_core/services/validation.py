"""alambic_core.services.validation — validation humaine des index extraits.

Porté de FlowerScan (transactions._validation_summary_from_tx + flux de validation).

Trois responsabilités :
- résumer l'état de validation d'une transaction (combien de documents validés,
  en attente, en erreur) pour l'affichage dans le tableau ;
- charger les index extraits d'un document pour l'écran de validation ;
- enregistrer les corrections d'un opérateur et faire passer un document à
  l'état VALIDATED.

Aucune I/O réseau ici : ces fonctions opèrent sur des objets/sessions fournis.
"""

from __future__ import annotations

from alambic_core.domain.enums import DocumentStatus
from alambic_core.models import Document, DocumentIndex

# Statuts considérés comme « validés » (le document a passé le contrôle humain
# ou est déjà exporté).
_VALIDATED = {DocumentStatus.VALIDATED.value, DocumentStatus.EXPORTED.value}
# Statuts « en attente » de validation humaine.
_PENDING = {DocumentStatus.PENDING_VALIDATION.value}
# Statuts en erreur.
_ERROR = {DocumentStatus.FAILED.value}
# Exclus du décompte (techniques / écartés).
_EXCLUDED = {DocumentStatus.DEPRECATED.value, DocumentStatus.DISCARDED.value}


def validation_summary(documents: list) -> dict:
    """Répartition validés / en attente / erreur des documents d'une transaction.

    Renvoie {validated, pending, error, total} sur les documents actifs
    (hors DEPRECATED/DISCARDED). Sert au tableau des transactions pour afficher
    par exemple « 3/5 validés ».
    """
    docs = [d for d in (documents or []) if _status_of(d) not in _EXCLUDED]
    validated = sum(1 for d in docs if _status_of(d) in _VALIDATED)
    pending = sum(1 for d in docs if _status_of(d) in _PENDING)
    error = sum(1 for d in docs if _status_of(d) in _ERROR)
    return {
        "validated": validated,
        "pending": pending,
        "error": error,
        "total": len(docs),
    }


def _status_of(doc) -> str:
    status = getattr(doc, "status", "")
    return status.value if hasattr(status, "value") else (status or "")


def load_indexes(session, document_id: str) -> list[dict]:
    """Charge les index extraits d'un document pour l'écran de validation.

    Renvoie une liste de {index_name, index_value, index_score, index_desc},
    triée par nom de champ. Seuls les index de type 'extracted' sont retournés
    (pas les métadonnées techniques).
    """
    rows = (
        session.query(DocumentIndex)
        .filter(
            DocumentIndex.document_id == document_id,
            DocumentIndex.index_type == "extracted",
        )
        .all()
    )
    rows.sort(key=lambda r: r.index_name or "")
    return [
        {
            "index_name": r.index_name,
            "index_value": r.index_value,
            "index_score": r.index_score,
            "index_desc": r.index_desc,
        }
        for r in rows
    ]


def save_indexes(session, document_id: str, indexes: list[dict]) -> int:
    """Enregistre les corrections d'index d'un opérateur.

    Remplace les index 'extracted' du document par ceux fournis (chaque entrée
    {index_name, index_value, [index_desc]}). Une correction humaine a un score
    de 1.0 (confiance maximale). Renvoie le nombre d'index écrits.

    Ne change pas le statut du document — c'est `validate_document` qui le fait.
    """
    session.query(DocumentIndex).filter(
        DocumentIndex.document_id == document_id,
        DocumentIndex.index_type == "extracted",
    ).delete()

    written = 0
    for idx in indexes or []:
        name = (idx.get("index_name") or "").strip()
        if not name:
            continue
        session.add(
            DocumentIndex(
                document_id=document_id,
                index_type="extracted",
                index_name=name,
                index_value=str(idx.get("index_value") or ""),
                index_score="1.0",  # corrigé/confirmé par un humain
                index_desc=idx.get("index_desc", "") or "",
            )
        )
        written += 1
    return written


def validate_document(session, document_id: str, indexes: list[dict] | None = None) -> bool:
    """Valide un document : enregistre d'éventuelles corrections puis passe à VALIDATED.

    Si `indexes` est fourni, il remplace les index extraits (corrections de
    l'opérateur) avant la validation. Renvoie True si le document existe et a été
    validé, False sinon.
    """
    doc = session.get(Document, document_id)
    if doc is None:
        return False
    if indexes is not None:
        save_indexes(session, document_id, indexes)
    doc.status = DocumentStatus.VALIDATED.value

    # Apprentissage incrémental : le doctype validé par l'humain enrichit le
    # modèle vectoriel (centroïdes d'embedding), ce qui réduit le recours au LLM
    # de classification. Best-effort — ne doit jamais faire échouer la validation.
    try:
        from alambic_core.models import Config, Transaction
        from alambic_core.services.vector_compactor import enrich_from_validation

        tx = session.get(Transaction, doc.transaction_id) if doc.transaction_id else None
        config = session.get(Config, tx.config_id) if (tx and tx.config_id) else None
        if config is not None:
            enrich_from_validation(config, doc)
    except Exception:  # noqa: BLE001
        pass

    return True
