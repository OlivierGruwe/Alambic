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

from alambic_core import storage
from alambic_core.ai.edenai_ocr import DocumentOcr, ocr_config_from_config
from alambic_core.ai.pdf_extractor import PdfExtractor
from alambic_core.ai.tesseract_ocr import tesseract_config_from_config
from alambic_core.db.session import session_scope
from alambic_core.models import Config, Document
from alambic_core.pipeline.step import step

logger = logging.getLogger(__name__)

PROCESS_OCR = "OCR_READER"


def _build_ocr_client(config):
    """Choisit le moteur OCR selon la config :
    - « tesseract » : local, gratuit, souverain (avec prétraitement) ;
    - « cascade »   : Tesseract d'abord, EdenAI en secours si score faible ;
    - défaut/« edenai » : EdenAI (cloud).
    Tous exposent ocr_bytes()->OcrResult, donc PdfExtractor les utilise pareil."""
    engine = ((config.edenai_settings or {}).get("ocr_engine") or "edenai").lower()
    if engine == "tesseract":
        return tesseract_config_from_config(config)
    if engine == "cascade":
        from alambic_core.ai.cascade_ocr import CascadeOcr

        return CascadeOcr(
            tesseract_config_from_config(config),
            DocumentOcr(ocr_config_from_config(config)),
        )
    return DocumentOcr(ocr_config_from_config(config))


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

        # Config OCR (moteur, provider, endpoint, clé déchiffrée).
        with session_scope() as s:
            config = s.get(Config, config_id) if config_id else None
            if config is None:
                logger.warning("OCR : config %s introuvable, étape sautée", config_id)
                payload["ocr"] = {"skipped": "no_config"}
                return payload
            ocr_client = _build_ocr_client(config)
            treat_images = bool((config.edenai_settings or {}).get("ocr_treat_images"))
            # Garde-fou taille image (optionnel) : Mpx max avant redimensionnement.
            raw_mpx = (config.edenai_settings or {}).get("ocr_max_pixels")

        # Téléchargement du PDF.
        work_dir = tempfile.mkdtemp(prefix="alambic_ocr_")
        local_pdf = os.path.join(work_dir, os.path.basename(key) or "doc.pdf")
        storage.download_to(bucket, key, local_pdf)

        # Conversion Mpx → pixels (config exprimée en millions de pixels).
        max_image_pixels = None
        try:
            if raw_mpx:
                max_image_pixels = int(float(raw_mpx) * 1_000_000)
        except (TypeError, ValueError):
            max_image_pixels = None

        # Extraction hybride (texte natif + OCR sélectif), barcodes réinjectés.
        extractor = PdfExtractor(
            local_pdf, ocr_client, treat_images=treat_images, barcodes=barcodes,
            max_image_pixels=max_image_pixels,
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

        # Trace du coût : TOUJOURS écrite (même à 0), avec le nombre de pages
        # pour calculer un coût unitaire par page.
        from alambic_core.services.cost_tracking import record_cost

        record_cost(
            process="OCR",
            amount=extractor.total_cost,
            transaction_id=tx_id,
            document_id=doc_id,
            account_id=account_id,
            provider=extractor.provider,
            model=extractor.model,
            pages=extractor.page_count,
        )

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
