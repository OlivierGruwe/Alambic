"""alambic_workers.tasks.split — étape de découpage (brique F).

Reconstruit les pages depuis l'OCR (ocr_lines + barcodes), applique le découpage
logique (doc_splitting : fixed_page / separator / multi-doc), puis matérialise
chaque groupe de pages en un document-enfant (PDF extrait + Document via
parent_id, comme la brique C). Si le document ne se découpe pas (un seul groupe),
on ne crée pas d'enfant : le document poursuit tel quel.
"""

from __future__ import annotations

import logging
import os
import tempfile

from alambic_core import storage
from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentProcess, DocumentStatus
from alambic_core.models import Config, Doctype, Document
from alambic_core.pipeline.step import step
from alambic_core.services.doc_splitting import split_pages

logger = logging.getLogger(__name__)

PROCESS_SPLIT = "DOC_SPLITTER"


def _doctype_fields(config_id: str | None) -> list[dict]:
    """Champs du doctype de la config (pour les séparateurs), ou []."""
    if not config_id:
        return []
    import json

    with session_scope() as s:
        config = s.get(Config, config_id)
        if config is None or not config.doctype_id:
            return []
        doctype = s.get(Doctype, config.doctype_id)
        if doctype is None or not doctype.json_content:
            return []
        try:
            data = json.loads(doctype.json_content)
        except (json.JSONDecodeError, TypeError):
            return []
        fields = data.get("fields")
        return fields if isinstance(fields, list) else []


def _pages_by_number(ocr_pages: list) -> dict:
    """Réindexe la liste de pages OCR (to_json) en {page_num: page}.

    Garantit que chaque page porte une clé `barcodes` (vide par défaut), car le
    découpage multi-document lit `page["barcodes"]`.
    """
    out = {}
    for p in ocr_pages or []:
        num = p.get("page")
        if num is not None:
            page = dict(p)
            page.setdefault("barcodes", [])
            out[int(num)] = page
    return out


def _inject_barcodes(pages: dict, barcodes: list) -> None:
    """Range les barcodes (liste à plat, avec n° de page) dans chaque page.

    readCAB produit une liste `[{value, page, format, position}, ...]` au niveau
    du document ; le découpage attend les barcodes regroupés par page. Cette
    fusion reconstitue `page["barcodes"]` à partir du numéro de page de chaque
    code-barres. Sans elle, detect_multidocument ne voit aucun code-barres et ne
    découpe jamais.
    """
    for bc in barcodes or []:
        num = bc.get("page")
        if num is None:
            continue
        page = pages.get(int(num))
        if page is not None:
            page.setdefault("barcodes", []).append(bc)


def _fixed_page_setting(config_id: str | None) -> int:
    """Valeur fixed_page de la config (0 si non défini).

    Lu depuis edenai_settings (bloc de paramétrage), clé 'fixed_page'.
    """
    if not config_id:
        return 0
    with session_scope() as s:
        config = s.get(Config, config_id)
        if config is None:
            return 0
        settings = config.edenai_settings or {}
        try:
            return int(settings.get("fixed_page", 0))
        except (TypeError, ValueError):
            return 0


def _child_ocr_from_parent(pages: dict, group: list[int]) -> tuple[list, str]:
    """Tranche d'OCR du parent pour un document-enfant (pages renumérotées à 1).

    `pages` est le dict {page_num: {lines, barcodes, ...}} du parent (déjà enrichi
    des barcodes). `group` est la liste des pages du parent qui composent l'enfant.
    Renvoie (ocr_lines, ocr_markdown) pour l'enfant, avec les pages renumérotées
    à partir de 1 — cohérent avec le PDF enfant, dont les pages repartent à 1.

    Évite de ré-OCRiser : on réutilise le texte déjà calculé sur le parent.
    """
    from alambic_core.ai.pdf_extractor import b64d

    child_lines = []
    md_parts = []
    for new_num, parent_num in enumerate(group, start=1):
        page = pages.get(parent_num)
        if page is None:
            continue
        lines = page.get("lines", []) or []
        child_lines.append(
            {
                "page": new_num,
                "lines": lines,
                "barcodes": page.get("barcodes", []) or [],
                "markdown": page.get("markdown", "") or "",
            }
        )
        # Markdown : page fournie (Mistral) sinon reconstruite depuis les lignes.
        page_md = (page.get("markdown") or "").strip()
        block = [f"[PAGE {new_num}]"]
        if page_md:
            block.append(page_md)
        else:
            for line in lines:
                txt = b64d(line["text"]).strip()
                if txt:
                    block.append(txt)
        md_parts.append("\n".join(block))
    return child_lines, "\n\n".join(md_parts).strip()


def _extract_pdf_pages(src_pdf: str, page_numbers: list[int], dest_pdf: str) -> None:
    """Extrait les pages données (1-indexées) du PDF source vers dest_pdf."""
    import fitz

    with fitz.open(src_pdf) as src:
        out = fitz.open()
        for pnum in page_numbers:
            idx = pnum - 1
            if 0 <= idx < src.page_count:
                out.insert_pdf(src, from_page=idx, to_page=idx)
        out.save(dest_pdf)
        out.close()


def split_document(payload: dict) -> dict:
    """Découpe le document en enfants si nécessaire. Renvoie le payload enrichi.

    payload["children"] : liste des documents-enfants créés (vide si pas de
    découpage). Si découpage, le document parent est déprécié.
    """
    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document") or {}
    doc_id = doc.get("documentId")
    config_id = payload.get("configId")
    file_info = doc.get("file", {})
    bucket = file_info.get("bucket", "")
    key = file_info.get("key", "")

    with step(tx_id, PROCESS_SPLIT, document_id=doc_id) as st:
        if st.skipped:
            return payload

        # Récupère l'OCR (pages positionnées) du document.
        with session_scope() as s:
            d = s.get(Document, doc_id)
            ocr_pages = d.ocr_lines if d is not None else []
            doc_barcodes = d.barcodes if d is not None else []

        pages = _pages_by_number(ocr_pages)
        if not pages:
            logger.info("Découpage sauté (pas d'OCR) pour le document %s", doc_id)
            payload["children"] = []
            return payload

        # Range les codes-barres (readCAB) par page : le découpage multi-document
        # lit page["barcodes"]. Source prioritaire : le payload (propagé par
        # read_cab) ; repli : la colonne documents.barcodes.
        barcodes = payload.get("barcodes") or doc_barcodes or []
        _inject_barcodes(pages, barcodes)

        fields = _doctype_fields(config_id)
        fixed_page = _fixed_page_setting(config_id)

        groups = split_pages(pages, fields=fields, fixed_page=fixed_page)

        # Un seul groupe → pas de découpage, le document continue tel quel.
        if len(groups) <= 1:
            logger.info("Document %s non découpé (1 seul document logique)", doc_id)
            payload["children"] = []
            return payload

        # Découpage : matérialise chaque groupe en document-enfant.
        work_dir = tempfile.mkdtemp(prefix="alambic_split_")
        local_pdf = os.path.join(work_dir, os.path.basename(key) or "doc.pdf")
        storage.download_to(bucket, key, local_pdf)

        children = []
        with session_scope() as s:
            for i, group in enumerate(groups, start=1):
                child_id = f"{doc_id}_split_{str(i).zfill(5)}"
                child_pdf = os.path.join(work_dir, f"{child_id}.pdf")
                _extract_pdf_pages(local_pdf, group, child_pdf)

                child_key = f"{os.path.dirname(key)}/{child_id}.pdf"
                storage.put_object(bucket, child_key, child_pdf)

                # Propage la tranche d'OCR du parent (texte déjà calculé) à
                # l'enfant, pages renumérotées à 1. Évite un ré-OCR et permet à
                # la classification/extraction de trouver le texte du sous-doc.
                child_lines, child_md = _child_ocr_from_parent(pages, group)
                child_barcodes = [bc for p in group for bc in pages.get(p, {}).get("barcodes", [])]

                if s.get(Document, child_id) is None:
                    s.add(
                        Document(
                            id=child_id,
                            transaction_id=tx_id,
                            parent_id=doc_id,
                            status=DocumentStatus.OCR_DONE.value,
                            process=DocumentProcess.NEWDOC.value,
                            bucket_name=bucket,
                            object_key=child_key,
                            ocr_markdown=child_md,
                            ocr_lines=child_lines,
                            barcodes=child_barcodes,
                        )
                    )
                children.append(
                    {
                        "documentId": child_id,
                        "pages": group,
                        "file": {"bucket": bucket, "key": child_key},
                    }
                )

            # Déprécie le parent (remplacé par ses enfants).
            parent = s.get(Document, doc_id)
            if parent is not None:
                parent.status = DocumentStatus.DEPRECATED.value

        payload["children"] = children
        logger.info("Document %s découpé en %d documents", doc_id, len(children))

    return payload
