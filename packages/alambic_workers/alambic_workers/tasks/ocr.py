"""alambic_workers.tasks.ocr — étape OCR (extraction de contenu hybride).

Lance le moteur d'extraction hybride (texte natif + OCR EdenAI sélectif) sur le
PDF du document, en réinjectant les codes-barres lus par readCAB. Persiste :
- ocr_markdown (texte structuré, pour la classification),
- ocr_lines (lignes positionnées, pour l'extraction de champs et le découpage),
et trace le coût EdenAI comme une ligne de la table costs, rattachée à la
transaction ET au document (agrégation du coût par transaction).
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import UTC, datetime

from alambic_core import storage
from alambic_core.ai.edenai_ocr import DocumentOcr, ocr_config_from_config
from alambic_core.ai.pdf_extractor import PdfExtractor
from alambic_core.db.session import session_scope
from alambic_core.models import Config, Cost, Document
from alambic_core.pipeline.step import step

logger = logging.getLogger(__name__)

PROCESS_OCR = "OCR_READER"


def _persist_cost(tx_id, doc_id, account_id, provider, model, amount) -> None:
    """Écrit une ligne de coût OCR (rattachée transaction + document)."""
    if not amount:
        return
    now = datetime.now(UTC)
    with session_scope() as s:
        s.add(
            Cost(
                account_id=account_id or None,
                transaction_id=tx_id,
                document_id=doc_id,
                amount=amount,
                provider=provider or "",
                model=model or "",
                process="OCR",
                details=f"{provider}/{model}",
                month=f"{now.month:02d}",
                year=str(now.year),
            )
        )


def read_ocr_document(payload: dict) -> dict:
    """OCR le document : extrait le contenu, persiste markdown + lignes, trace le coût."""
    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document") or {}
    doc_id = doc.get("documentId")
    config_id = payload.get("configId")
    account_id = payload.get("accountId")
    file_info = doc.get("file", {})
    bucket = file_info.get("bucket", "")
    key = file_info.get("key", "")
    barcodes = payload.get("barcodes", [])

    with step(tx_id, PROCESS_OCR, document_id=doc_id) as st:
        if st.skipped:
            return payload

        # Config OCR (provider, endpoint, clé déchiffrée).
        with session_scope() as s:
            config = s.get(Config, config_id) if config_id else None
            if config is None:
                logger.warning("OCR : config %s introuvable, étape sautée", config_id)
                payload["ocr"] = {"skipped": "no_config"}
                return payload
            ocr_conf = ocr_config_from_config(config)
            treat_images = bool((config.edenai_settings or {}).get("ocr_treat_images"))

        # Téléchargement du PDF.
        work_dir = tempfile.mkdtemp(prefix="alambic_ocr_")
        local_pdf = os.path.join(work_dir, os.path.basename(key) or "doc.pdf")
        storage.download_to(bucket, key, local_pdf)

        # Extraction hybride (texte natif + OCR sélectif), barcodes réinjectés.
        ocr_client = DocumentOcr(ocr_conf)
        extractor = PdfExtractor(
            local_pdf, ocr_client, treat_images=treat_images, barcodes=barcodes
        )
        extractor.parse()

        markdown = extractor.to_markdown()
        ocr_json = extractor.to_json()

        # Persistance sur le document.
        with session_scope() as s:
            d = s.get(Document, doc_id)
            if d is not None:
                d.ocr_markdown = markdown
                d.ocr_lines = ocr_json.get("pages", [])

        # Trace du coût (best-effort : ne casse pas l'étape).
        try:
            _persist_cost(
                tx_id,
                doc_id,
                account_id,
                extractor.provider,
                extractor.model,
                extractor.total_cost,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR : coût non enregistré : %s", exc)

        payload["ocr"] = {
            "pages": extractor.page_count,
            "provider": extractor.provider,
            "cost": extractor.total_cost,
        }
        logger.info(
            "OCR : %d page(s), provider=%s, coût=%s (document %s)",
            extractor.page_count,
            extractor.provider,
            extractor.total_cost,
            doc_id,
        )

    return payload
