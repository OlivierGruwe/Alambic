"""alambic_workers.tasks.barcode — étape readCAB (lecture des codes-barres).

Gating : on ne lit les codes-barres que si le doctype de la config a un champ
avec bcr_type non vide (cf. barcode_gating). Sinon on saute (gain de temps).

Si on lit : on télécharge le PDF (déjà converti à l'étape précédente), on lit les
codes-barres (barcode.read_barcodes), on les persiste sur le document (champ
barcodes) ET dans le payload (pour le découpage en aval).
"""

from __future__ import annotations

import logging
import os
import tempfile

from alambic_core import storage
from alambic_core.db.session import session_scope
from alambic_core.models import Config, Doctype, Document
from alambic_core.pipeline.step import step
from alambic_core.services.barcode_gating import doctype_needs_cab

from alambic_workers.barcode import read_barcodes

logger = logging.getLogger(__name__)

PROCESS_CAB = "CAB_READER"


def _config_doctype_json(config_id: str | None) -> str | None:
    """json_content du doctype de la config, ou None si introuvable."""
    if not config_id:
        return None
    from alambic_core.services.completeness import doctype_ids_from_expected

    with session_scope() as s:
        config = s.get(Config, config_id)
        if config is None:
            return None
        # Avant classification : on concatène le json_content de tous les doctypes
        # attendus (repli doctype_id) pour détecter une stratégie code-barres.
        doctype_ids = doctype_ids_from_expected(config) or (
            [config.doctype_id] if config.doctype_id else []
        )
        contents = []
        for did in doctype_ids:
            doctype = s.get(Doctype, did)
            if doctype is not None and doctype.json_content:
                contents.append(doctype.json_content)
        return "\n".join(contents) if contents else None


def read_cab_document(payload: dict) -> dict:
    """Lit les codes-barres du document si son doctype l'exige.

    Renvoie le payload enrichi de payload["barcodes"] (liste, éventuellement vide).
    """
    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document") or {}
    doc_id = doc.get("documentId")
    config_id = payload.get("configId")
    file_info = doc.get("file", {})
    bucket = file_info.get("bucket", "")
    key = file_info.get("key", "")

    with step(tx_id, PROCESS_CAB, document_id=doc_id) as st:
        if st.skipped:
            return payload

        # Gating : le doctype de la config demande-t-il la lecture CAB ?
        doctype_json = _config_doctype_json(config_id)
        if doctype_json is None or not doctype_needs_cab(doctype_json):
            logger.info("readCAB sauté (aucun bcr_type) pour le document %s", doc_id)
            payload["barcodes"] = []
            return payload

        # Lecture : télécharge le PDF puis scanne les codes-barres.
        work_dir = tempfile.mkdtemp(prefix="alambic_cab_")
        local_pdf = os.path.join(work_dir, os.path.basename(key) or "doc.pdf")
        storage.download_to(bucket, key, local_pdf)

        barcodes = read_barcodes(local_pdf)

        # Persistance en base (lecture rapide ensuite, sans I/O réseau).
        with session_scope() as s:
            d = s.get(Document, doc_id)
            if d is not None:
                d.barcodes = barcodes

        payload["barcodes"] = barcodes
        logger.info("readCAB : %d code(s)-barres pour le document %s", len(barcodes), doc_id)

    return payload
