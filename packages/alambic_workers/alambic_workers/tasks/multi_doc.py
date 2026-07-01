"""alambic_workers.tasks.multi_doc — détection de plusieurs documents par page.

Étape de pipeline placée APRÈS l'OCR et AVANT le découpage par séparateur :

    read_ocr → multi_doc → detect_split → classify → extract → finalize

Si la page contient plusieurs documents physiques distincts (ex. CNI + carte
grise photographiées ensemble), on crée N sous-documents croppés (un par bbox),
chacun ré-OCRisé et classifié séparément. Sinon le document poursuit tel quel.

Souveraineté : la détection est 100% locale (vision par ordinateur OpenCV, cf.
alambic_core.vision.document_segmenter). Aucun appel externe, aucun coût : on
analyse l'image de la page pour repérer les régions rectangulaires de contenu
distinctes et on découpe. Adapté aux pages monopages en image (photo/photocopie).
"""

from __future__ import annotations

import io
import logging
import os
import tempfile

from alambic_core import storage
from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentProcess, DocumentStatus
from alambic_core.models import Config, Document
from alambic_core.pipeline.step import step
from alambic_core.vision import segment_from_png_bytes

logger = logging.getLogger(__name__)

PROCESS_MULTI_DOC = "DETECT_MULTI_DOC"

# Marge ajoutée autour de chaque bbox au crop (en fraction de la dimension), pour
# ne pas rogner les bords d'un document dont la bbox estimée serait un peu serrée.
_CROP_MARGIN = 0.02

# Plafonds de résolution pour borner l'empreinte mémoire (un rendu non borné de
# scan haute résolution fait sauter le worker par OOM → arrêt « warm stop »).
# Détection : envoyée à Pixtral, qui comprend la disposition à basse résolution.
_DETECT_MAX_PIXELS = 2_000_000  # ~2 Mpx suffisent pour repérer des documents
# Crop : devient le sous-document ré-OCRisé, on garde plus de finesse mais borné.
_CROP_MAX_PIXELS = 8_000_000  # ~8 Mpx = lisible pour l'OCR sans exploser la RAM


def _zoom_for(page, max_pixels: int, min_pixels: int = 2_000_000) -> float:
    """Facteur de zoom pour rester dans [min_pixels, max_pixels].

    Un PDF défini en points (72 dpi) rend en très basse résolution à zoom 1 —
    insuffisant pour la segmentation. On garantit donc un PLANCHER (agrandissement
    si la page est petite) tout en respectant le PLAFOND anti-OOM. Si les deux
    bornes sont incompatibles (page minuscule), le plafond prime.
    """
    rect = page.rect
    base_px = max(1.0, rect.width * rect.height)  # pixels à zoom 1 (72 dpi)
    if base_px < min_pixels:
        zoom = (min_pixels / base_px) ** 0.5  # agrandir jusqu'au plancher
    elif base_px > max_pixels:
        zoom = (max_pixels / base_px) ** 0.5  # réduire jusqu'au plafond
    else:
        zoom = 1.0
    # Ne jamais dépasser le plafond même après application du plancher.
    if base_px * zoom * zoom > max_pixels:
        zoom = (max_pixels / base_px) ** 0.5
    return zoom


def _first_page_to_png(pdf_bytes: bytes, max_pixels: int = _DETECT_MAX_PIXELS) -> bytes:
    """Rend la 1re page d'un PDF en PNG, résolution bornée à max_pixels."""
    import fitz

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc[0]
        zoom = _zoom_for(page, max_pixels)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        try:
            return pix.tobytes("png")
        finally:
            # Libère explicitement la bitmap (grosse) sans attendre le GC.
            pix = None


def _crop_to_pdf(image_bytes: bytes, bbox: dict) -> bytes:
    """Crop la région bbox (en %) d'une image et renvoie un PDF mono-page.

    Une marge de sécurité est ajoutée autour de la bbox pour éviter de couper
    les bords du document.
    """
    import fitz
    from PIL import Image

    with Image.open(io.BytesIO(image_bytes)) as img:
        w, h = img.size

        x = bbox["x"] / 100 * w
        y = bbox["y"] / 100 * h
        bw = bbox["w"] / 100 * w
        bh = bbox["h"] / 100 * h

        mx = _CROP_MARGIN * w
        my = _CROP_MARGIN * h
        x1 = max(0, int(x - mx))
        y1 = max(0, int(y - my))
        x2 = min(w, int(x + bw + mx))
        y2 = min(h, int(y + bh + my))

        cropped = img.crop((x1, y1, x2, y2))

    png_buf = io.BytesIO()
    cropped.save(png_buf, format="PNG")
    png_bytes = png_buf.getvalue()
    cw, ch = cropped.width, cropped.height
    cropped.close()

    pdf_doc = fitz.open()
    rect = fitz.Rect(0, 0, cw, ch)
    page = pdf_doc.new_page(width=cw, height=ch)
    page.insert_image(rect, stream=png_bytes)
    pdf_buf = io.BytesIO()
    pdf_doc.save(pdf_buf)
    pdf_doc.close()
    return pdf_buf.getvalue()


def detect_multi_doc(payload: dict) -> dict:
    """Détecte les documents multiples d'une page et crée les sous-docs.

    payload["children"] : liste des sous-documents créés (vide si mono-document).
    Le document parent est déprécié quand il est remplacé par ses enfants.
    """
    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document") or {}
    doc_id = doc.get("documentId")
    config_id = payload.get("configId")
    file_info = doc.get("file") or {}
    bucket = file_info.get("bucket") or doc.get("bucket", "")
    key = file_info.get("key") or doc.get("key", "")

    with step(tx_id, PROCESS_MULTI_DOC, document_id=doc_id) as st:
        if st.skipped:
            return payload

        # Garde anti-boucle : un sous-doc issu d'un précédent multi-doc ne doit
        # pas être re-détecté (le crop ne contient qu'un seul document).
        if doc.get("source") == "multi_doc_split":
            payload["children"] = []
            return payload

        with session_scope() as s:
            config = s.get(Config, config_id) if config_id else None
            if config is None:
                payload["children"] = []
                return payload

            if not config.multi_doc_detect:
                # Fonctionnalité désactivée pour cette config.
                payload["children"] = []
                return payload

        # 1. Page → image → segmentation OpenCV (locale, gratuite, souveraine).
        # On rend directement en résolution « crop » : une seule image sert à la
        # fois à détecter et à découper (plus d'appel réseau à alléger). La
        # résolution reste bornée pour ne pas saturer la RAM du worker.
        try:
            pdf_bytes = storage.get_bytes(bucket, key)
            page_image = _first_page_to_png(pdf_bytes, max_pixels=_CROP_MAX_PIXELS)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Multi-doc : lecture/rendu impossible pour %s : %s", doc_id, exc)
            payload["children"] = []
            return payload
        pdf_bytes = None  # plus besoin du PDF source en RAM

        result = segment_from_png_bytes(page_image)

        # 2. Mono-document → rien à découper (aucun coût : traitement local).
        if not result.is_multi:
            payload["children"] = []
            payload["multi_doc"] = {"detected": False, "count": 1, "method": result.method}
            return payload

        # 3. Multi-document → crop de chaque région détectée en sous-document.
        crop_image = page_image
        children = []
        with tempfile.TemporaryDirectory() as work_dir, session_scope() as s:
            for i, det in enumerate(result.documents, start=1):
                try:
                    sub_pdf_bytes = _crop_to_pdf(crop_image, det["bbox"])
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Multi-doc : crop %d échoué pour %s : %s", i, doc_id, exc)
                    continue

                child_id = f"{doc_id}_mdoc_{str(i).zfill(5)}"
                child_key = f"{os.path.dirname(key)}/{child_id}.pdf"

                local_pdf = os.path.join(work_dir, f"{child_id}.pdf")
                with open(local_pdf, "wb") as fh:
                    fh.write(sub_pdf_bytes)
                storage.put_object(bucket, child_key, local_pdf)

                if s.get(Document, child_id) is None:
                    s.add(
                        Document(
                            id=child_id,
                            transaction_id=tx_id,
                            parent_id=doc_id,
                            # Le crop est une image neuve : pas d'OCR hérité, le
                            # sous-doc repart en CONVERTED_TO_PDF pour être ré-OCRisé.
                            status=DocumentStatus.CONVERTED_TO_PDF.value,
                            # Le crop est un PDF-image neuf : il doit RepartIR avant
                            # l'OCR. On le marque FILE_CONVERTED (phase conversion,
                            # antérieure à OCR_READER) — sinon le step OCR le
                            # considérerait « déjà passé » et sauterait l'OCR, et le
                            # sous-doc arriverait vide à la classification.
                            process=DocumentProcess.FILE_CONVERTED.value,
                            bucket_name=bucket,
                            object_key=child_key,
                        )
                    )
                children.append(
                    {
                        "documentId": child_id,
                        "type": det.get("type", ""),
                        "file": {"bucket": bucket, "key": child_key},
                        "source": "multi_doc_split",
                    }
                )

            # Déprécie le parent : il est remplacé par ses sous-documents.
            if children:
                parent = s.get(Document, doc_id)
                if parent is not None:
                    parent.status = DocumentStatus.DEPRECATED.value

        payload["children"] = children
        payload["multi_doc"] = {"detected": bool(children), "count": len(children)}
        logger.info("Document %s : %d sous-documents détectés", doc_id, len(children))
        return payload
