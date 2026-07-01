"""alambic_core.ai.pdf_extractor — moteur d'extraction de contenu PDF (hybride).

Porté de FlowerScan (FclPdfFile). Pour chaque page, choisit la stratégie la
moins coûteuse :

- NATIVE (PDF avec couche texte) : extraction du texte natif positionné. PAS
  d'OCR → gratuit.
- SCAN (image pure) : OCR full-page (avec positions précises si le provider les
  fournit, sinon texte plat).
- HYBRID : texte natif + OCR des images + OCR des zones vides (si peu de lignes).

Les codes-barres (lus en amont par readCAB) sont injectés dans le flux de lignes
de leur page. Traitement multi-thread par page (ThreadPoolExecutor), protégé par
un Lock (accès document fitz) et un Semaphore (limite d'appels OCR concurrents).

Sorties :
- to_json() : {"pages": [{"page", "lines":[{text(b64), position(%)}], ...}]}
- to_markdown() : texte structuré [PAGE N] pour la classification.

Le texte des lignes est encodé en base64 (comme FlowerScan) pour stocker sans
souci tout caractère (JSON-safe). Décodé par to_markdown / le consommateur.
"""

from __future__ import annotations

import base64
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Semaphore

logger = logging.getLogger(__name__)

TOKEN_PATTERN = re.compile(r"\b[a-z0-9]{3,}\b")
MAX_IMAGE_PIXELS = 12_000_000


def b64e(text: str) -> str:
    """Encode un texte en base64 (JSON-safe pour le stockage des lignes)."""
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def b64d(data: str) -> str:
    """Décode un texte base64. Tolérant : renvoie '' si invalide."""
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _position_percent(x0, y0, x1, y1, w, h) -> dict:
    """Coordonnées pixel → pourcentages (0..100) de la page."""
    if not w or not h:
        return {"x0": 0.0, "y0": 0.0, "x1": 100.0, "y1": 100.0}
    return {
        "x0": round(x0 / w * 100, 4),
        "y0": round(y0 / h * 100, 4),
        "x1": round(x1 / w * 100, 4),
        "y1": round(y1 / h * 100, 4),
    }


def detect_page_type(page) -> str:
    """Classe une page : NATIVE (texte), SCAN (image), ou HYBRID."""
    blocks = page.get_text("blocks")

    text_chars = 0
    for b in blocks:
        text = b[4].strip()
        if not text:
            continue
        text_chars += len(text)

    image_count = len(page.get_images(full=True))

    # SCAN : quasi aucun texte extractible mais des images → vraie page scannée.
    if text_chars < 20 and image_count > 0:
        return "SCAN"

    # NATIVE : assez de texte natif extractible. Le NOMBRE DE CARACTÈRES est le
    # signal primaire — un courrier ou un contrat est massivement natif même si
    # le texte ne couvre que 20-30% de la surface (marges, interlignes, blanc).
    # Le ratio de surface est trompeur pour les documents bureautiques aérés :
    # exiger >0.6 classait à tort en HYBRID des pages 100% texte, déclenchant un
    # OCR EdenAI inutile (coûteux et lent) sur du PDF déjà lisible.
    if text_chars >= 200:
        return "NATIVE"

    # Texte présent mais peu abondant, avec des images → zones à OCR-iser.
    if image_count > 0:
        return "HYBRID"

    # Peu de texte, pas d'image : page pauvre mais native (rien à OCR-iser).
    return "NATIVE"


def _merge_zones_2d(zones):
    """Fusionne les rectangles qui s'intersectent (pour les zones vides)."""
    merged = []
    for zone in zones:
        added = False
        for i, m in enumerate(merged):
            if m.intersects(zone):
                merged[i] = m | zone
                added = True
                break
        if not added:
            merged.append(zone)
    return merged


class PdfExtractor:
    """Extrait le contenu d'un PDF en lignes positionnées (texte natif + OCR).

    `ocr` est un objet exposant ocr(file_path|bytes, filename) -> OcrResult
    (cf. DocumentOcr). `treat_images` active l'OCR des images intégrées.
    `barcodes` : liste {value, page, position} de readCAB, injectée par page.
    """

    def __init__(self, pdf_path, ocr, *, treat_images=False, barcodes=None, max_pages=30,
                 max_image_pixels=None):
        self.pdf_path = pdf_path
        self.ocr = ocr
        self.treat_images = treat_images
        self.barcodes = barcodes or []
        self.max_pages = max_pages
        # Garde-fou taille image (None → défaut du module). Configurable pour
        # ajuster le compromis qualité OCR / vitesse selon le parc documentaire.
        from alambic_core.ai.image_preprocess import MAX_IMAGE_PIXELS

        self.max_image_pixels = max_image_pixels or MAX_IMAGE_PIXELS

        self.pages: list[dict] = []
        self.page_count = 0
        self.total_cost = 0.0
        self.provider = ""
        self.model = ""

        # Initialisés dans __init__ (FIX FlowerScan : sinon un thread pouvait
        # démarrer avant leur création → AttributeError).
        self._lock = Lock()
        self._sema = Semaphore(3)

    # ── Stratégies par page ─────────────────────────────────────────────────
    def _clean_text(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"[\x00-\x1F\x7F]", "", text)
        text = text.strip()
        return text if len(text) >= 2 else ""

    def _extract_native_text(self, page, pw, ph) -> list:
        import fitz

        lines = []
        try:
            blocks = sorted(page.get_text("blocks"), key=lambda b: (b[1], b[0]))
            for b in blocks:
                text = self._clean_text(b[4])
                if not text:
                    continue
                rect = fitz.Rect(b[:4])
                lines.append(
                    {
                        "text": b64e(text),
                        "position": _position_percent(rect.x0, rect.y0, rect.x1, rect.y1, pw, ph),
                    }
                )
        except Exception as ex:  # noqa: BLE001
            logger.warning("Texte natif illisible : %s", ex)
        return lines

    def _record_ocr(self, ocr_res) -> None:
        """Accumule coût + provider/model (sous lock, thread-safe)."""
        from contextlib import suppress

        with self._lock, suppress(TypeError, ValueError):
            self.total_cost += float(ocr_res.cost or 0)
            if ocr_res.provider and not self.provider:
                self.provider = ocr_res.provider
                self.model = ocr_res.model

    def _ocr_bytes(self, data: bytes, filename: str):
        # Garde-fou taille : borne les octets image avant TOUT moteur (Tesseract
        # local comme EdenAI distant). Pour EdenAI, réduit aussi le volume réseau
        # et le coût. Sans effet sous le seuil.
        from alambic_core.ai.image_preprocess import guard_image_bytes

        data = guard_image_bytes(data, max_pixels=self.max_image_pixels, filename=filename)
        with self._sema:
            return self.ocr.ocr_bytes(data, filename)

    def _append_ocr_lines(self, lines, ocr_res, position) -> None:
        text = self._clean_text(ocr_res.text or "")
        if not text:
            return
        for part in (p.strip() for p in text.split("\n") if p.strip()):
            if len(part) < 2 or not TOKEN_PATTERN.search(part.lower()):
                continue
            lines.append({"text": b64e(part), "position": position})

    def _ocr_full_page(self, page, page_index, pw, ph):
        import fitz

        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
        ocr_res = self._ocr_bytes(pix.tobytes("png"), f"full_{page_index}.png")
        self._record_ocr(ocr_res)

        # Markdown structuré de cette page si le provider le fournit (Mistral).
        page_md = ""
        for pm in getattr(ocr_res, "pages_markdown", []) or []:
            # Mistral numérote ses pages depuis 0 ; ici on OCR une page à la fois
            # (image rendue), donc on prend le premier markdown renvoyé.
            page_md = pm.get("markdown", "")
            if page_md:
                break

        # Positions précises si le provider les a fournies (Amazon/Textract).
        positioned = ocr_res.lines or []
        if positioned:
            lines = []
            for ln in positioned:
                txt = self._clean_text(ln.get("text", "") or "")
                if not txt:
                    continue
                bbox = ln.get("bbox") or {}
                lines.append(
                    {
                        "text": b64e(txt),
                        "position": {
                            "x0": bbox.get("x0", 0.0),
                            "y0": bbox.get("y0", 0.0),
                            "x1": bbox.get("x1", 100.0),
                            "y1": bbox.get("y1", 100.0),
                        },
                    }
                )
            if lines:
                return lines, page_md

        # Fallback : texte plat au bbox de la page entière.
        text = (ocr_res.text or "").strip()
        if not text:
            return [], page_md
        return [{"text": b64e(text), "position": _position_percent(0, 0, pw, ph, pw, ph)}], page_md

    def _get_empty_zones(self, page, text_rects):
        import fitz

        page_rect = page.rect
        if not text_rects:
            return [page_rect]
        merged = _merge_zones_2d(text_rects)
        empty = []
        current_y = 0
        for rect in sorted(merged, key=lambda r: r.y0):
            if rect.y0 > current_y:
                empty.append(fitz.Rect(0, current_y, page_rect.width, rect.y0))
            current_y = max(current_y, rect.y1)
        if current_y < page_rect.height:
            empty.append(fitz.Rect(0, current_y, page_rect.width, page_rect.height))
        return empty

    def _extract_hybrid_zones(self, page, page_index, pw, ph) -> list:
        import fitz

        lines = []
        blocks = page.get_text("blocks")
        text_rects = [fitz.Rect(b[:4]) for b in blocks if b[4].strip()]
        text_area = sum(r.get_area() for r in text_rects)
        page_area = pw * ph
        if page_area and text_area / page_area > 0.7:
            return []

        matrix = fitz.Matrix(300 / 72, 300 / 72)
        for zone in self._get_empty_zones(page, text_rects):
            try:
                pix = page.get_pixmap(matrix=matrix, clip=zone)
                ocr_res = self._ocr_bytes(pix.tobytes("png"), f"zone_{page_index}.png")
                self._record_ocr(ocr_res)
                text = (ocr_res.text or "").strip()
                if not text:
                    continue
                lines.append(
                    {
                        "text": b64e(text),
                        "position": _position_percent(zone.x0, zone.y0, zone.x1, zone.y1, pw, ph),
                    }
                )
            except Exception as ex:  # noqa: BLE001
                logger.warning("OCR zone page %s : %s", page_index, ex)
        return lines

    def _inject_barcodes(self, page_number, pw, ph) -> list:
        lines = []
        if not self.barcodes:
            return lines
        seen = set()
        for bc in self.barcodes:
            try:
                if bc.get("page") != page_number:
                    continue
                value = self._clean_text(bc.get("value", ""))
                position = bc.get("position")
                if not value or not position or value in seen:
                    continue
                seen.add(value)
                lines.append({"text": b64e(value), "position": position, "source": "barcode"})
            except Exception as ex:  # noqa: BLE001
                logger.warning("Injection barcode page %s : %s", page_number, ex)
        return lines

    def _process_page(self, page_index, page) -> dict | None:
        try:
            pw, ph = page.mediabox_size
            page_type = detect_page_type(page)
            page_md = ""

            if page_type == "NATIVE":
                lines = self._extract_native_text(page, pw, ph)
            elif page_type == "SCAN":
                lines, page_md = self._ocr_full_page(page, page_index, pw, ph)
            else:  # HYBRID
                lines = self._extract_native_text(page, pw, ph)
                if self.treat_images or len(lines) < 20:
                    lines += self._extract_hybrid_zones(page, page_index, pw, ph)

            lines.extend(self._inject_barcodes(page_index + 1, pw, ph))
            return {"page": page_index + 1, "lines": lines, "markdown": page_md}
        except Exception as ex:  # noqa: BLE001
            logger.warning("Page %s en échec : %s", page_index, ex)
            return None

    # ── Pilotage ─────────────────────────────────────────────────────────────
    def parse(self) -> None:
        import fitz

        with fitz.open(self.pdf_path) as doc:
            self.page_count = min(doc.page_count, self.max_pages)
            pages = [doc.load_page(i) for i in range(self.page_count)]
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(self._process_page, i, p) for i, p in enumerate(pages)]
                for f in as_completed(futures):
                    r = f.result()
                    if r:
                        self.pages.append(r)
        self.pages.sort(key=lambda x: x["page"])

    def to_json(self) -> dict:
        return {"pages": self.pages}

    def to_markdown(self) -> str:
        """Texte structuré [PAGE N] pour la classification.

        Format UNIQUE quelle que soit la source : si la page a un markdown
        fourni par le provider (Mistral), on l'utilise ; sinon on reconstruit
        depuis les lignes (texte natif PyMuPDF ou OCR positionné).
        """
        parts = []
        for page in self.pages:
            page_parts = [f"[PAGE {page['page']}]"]
            page_md = (page.get("markdown") or "").strip()
            if page_md:
                page_parts.append(page_md)
            else:
                for line in page["lines"]:
                    txt = b64d(line["text"]).strip()
                    if txt:
                        page_parts.append(txt)
            parts.append("\n".join(page_parts))
        return "\n\n".join(parts).strip()
