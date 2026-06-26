"""alambic_workers.conversion — conversion des documents au format pivot PDF.

Porté de flowerscan_lib (convert_text, convert_image, convert_office) avec
corrections :
- pas de dépendance à un s3obj global : on travaille sur des chemins locaux ;
- erreurs loguées (logging.exception), pas d'except muet ;
- détection de type robuste (signature + extension).

Trois convertisseurs :
- convert_text  : fichier texte → PDF (FPDF, police mono, encodage détecté).
- convert_image : image (y compris TIFF multi-frame) → PDF (Pillow + reportlab).
- convert_office: document bureautique → PDF (LibreOffice headless).

Un PDF en entrée n'est pas converti (déjà au format pivot).

La conversion Office nécessite LibreOffice (soffice) installé sur le worker.
Elle est routée vers une queue Celery dédiée (« office ») pour être isolée et
multipliable indépendamment du reste du pipeline.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

logger = logging.getLogger(__name__)

# Catégories de conversion.
KIND_PDF = "pdf"
KIND_TEXT = "text"
KIND_IMAGE = "image"
KIND_OFFICE = "office"
KIND_UNKNOWN = "unknown"

_TEXT_EXTS = {".txt", ".csv", ".log", ".md", ".json", ".xml", ".html", ".htm"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp"}
_OFFICE_EXTS = {
    ".doc",
    ".docx",
    ".odt",
    ".rtf",
    ".xls",
    ".xlsx",
    ".ods",
    ".ppt",
    ".pptx",
    ".odp",
}


def is_office_key(key: str) -> bool:
    """True si la clé/le nom de fichier désigne un document bureautique."""
    return Path(key).suffix.lower() in _OFFICE_EXTS


def detect_kind(path: str) -> str:
    """Détermine la catégorie de conversion d'un fichier local.

    Signature (magic bytes) en priorité pour PDF et images, repli sur extension.
    """
    p = Path(path)
    ext = p.suffix.lower()

    try:
        with open(path, "rb") as f:
            head = f.read(8)
    except OSError:
        head = b""

    # PDF : signature %PDF.
    if head[:4] == b"%PDF" or ext == ".pdf":
        return KIND_PDF
    # Images : quelques signatures courantes.
    if (
        head[:8] == b"\x89PNG\r\n\x1a\n"
        or head[:2] == b"\xff\xd8"  # JPEG
        or head[:2] in (b"II", b"MM")  # TIFF
        or head[:2] == b"BM"  # BMP
        or head[:4] == b"GIF8"
    ):
        return KIND_IMAGE
    if ext in _IMAGE_EXTS:
        return KIND_IMAGE
    if ext in _OFFICE_EXTS:
        return KIND_OFFICE
    if ext in _TEXT_EXTS:
        return KIND_TEXT
    return KIND_UNKNOWN


# ── Conversion texte → PDF ──────────────────────────────────────────────────
_TAB_SIZE = 4


def _detect_encoding(path: str, sample: int = 4096) -> str:
    import chardet

    with open(path, "rb") as f:
        raw = f.read(sample)
    return chardet.detect(raw).get("encoding") or "utf-8"


def convert_text(filepath: str, out_dir: str) -> tuple[str, int]:
    """Convertit un fichier texte en PDF fidèle. Renvoie (chemin_pdf, nb_pages)."""
    from fpdf import FPDF
    from fpdf.errors import FPDFUnicodeEncodingException

    output_pdf = Path(out_dir) / "converted.pdf"
    pdf = FPDF("P", "mm", "A4")
    pdf.set_margins(15, 15, 15)
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    font_size = 10
    pdf.set_font("Courier", size=font_size)
    line_height = font_size * 0.35
    usable_width = pdf.w - pdf.l_margin - pdf.r_margin
    char_width = pdf.get_string_width("M") or 1
    max_chars = max(1, int(usable_width / char_width))

    encoding = _detect_encoding(filepath)

    def _clean(s: str) -> str:
        # Remplace les caractères que la police ne peut pas encoder.
        probe = FPDF()
        probe.add_page()
        probe.set_font("Helvetica", size=10)
        while True:
            try:
                probe.cell(200, 10, text=s, new_x="LEFT", new_y="NEXT")
                return s
            except FPDFUnicodeEncodingException as ex:
                s = s.replace(ex.character, "?")

    with open(filepath, encoding=encoding, errors="replace") as f:
        for raw_line in f:
            line = raw_line.replace("\t", " " * _TAB_SIZE)
            line = _clean(line.rstrip("\n"))
            if line == "":
                pdf.ln(line_height)
                continue
            for part in textwrap.wrap(
                line,
                width=max_chars,
                replace_whitespace=False,
                drop_whitespace=False,
            ):
                pdf.cell(0, line_height, part, new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(output_pdf))
    return str(output_pdf), pdf.page_no()


# ── Conversion image → PDF ──────────────────────────────────────────────────
_MAX_IMAGE_PX = 10000
_DEFAULT_DPI = 200
_MARGIN_MM = 10.0


def convert_image(filepath: str, out_dir: str) -> tuple[str, int]:
    """Convertit une image (multi-frame possible) en PDF A4. Renvoie (pdf, pages)."""
    import gc

    from PIL import Image, ImageSequence
    from reportlab.lib.pagesizes import A4, landscape, portrait
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    Image.MAX_IMAGE_PIXELS = 50_000_000
    margin_pt = _MARGIN_MM * mm
    output_pdf = Path(out_dir) / "converted.pdf"
    pdf = canvas.Canvas(str(output_pdf), pagesize=A4)
    nb_pages = 0

    with Image.open(filepath) as img:
        img_dpi = img.info.get("dpi", (_DEFAULT_DPI, _DEFAULT_DPI))[0] or _DEFAULT_DPI
        for frame_index, frame in enumerate(ImageSequence.Iterator(img)):
            try:
                if frame.mode == "P":
                    frame = frame.convert("RGB")
                if frame.width > _MAX_IMAGE_PX or frame.height > _MAX_IMAGE_PX:
                    frame.thumbnail((_MAX_IMAGE_PX, _MAX_IMAGE_PX), Image.BILINEAR)

                w_px, h_px = frame.size
                w_pt = w_px / img_dpi * 72
                h_pt = h_px / img_dpi * 72
                page_size = landscape(A4) if w_pt > h_pt else portrait(A4)
                pdf.setPageSize(page_size)
                page_w, page_h = page_size
                scale = min(
                    (page_w - 2 * margin_pt) / w_pt,
                    (page_h - 2 * margin_pt) / h_pt,
                )
                draw_w, draw_h = w_pt * scale, h_pt * scale
                x = (page_w - draw_w) / 2
                y = (page_h - draw_h) / 2
                pdf.drawImage(
                    ImageReader(frame),
                    x,
                    y,
                    width=draw_w,
                    height=draw_h,
                    preserveAspectRatio=True,
                    mask="auto",
                )
                pdf.showPage()
                nb_pages += 1
            except Exception:
                logger.exception("Frame %s ignorée dans %s", frame_index, filepath)
            finally:
                gc.collect()

    pdf.save()
    return str(output_pdf), nb_pages


# ── Conversion Office → PDF (LibreOffice headless) ──────────────────────────
def convert_office(filepath: str, out_dir: str) -> tuple[str, int]:
    """Convertit un document bureautique en PDF via LibreOffice. (pdf, pages).

    Nécessite soffice installé (cf. image du worker Office). Lancé en headless,
    sans interaction. Le HOME est forcé sur un dossier temporaire pour éviter
    les soucis de profil LibreOffice en environnement concurrent.
    """
    import os
    import shutil
    import subprocess

    input_file = Path(filepath)
    output_pdf = Path(out_dir) / "converted.pdf"
    work = Path(out_dir) / "__office__"
    work.mkdir(parents=True, exist_ok=True)

    soffice = (
        os.environ.get("ALAMBIC_SOFFICE_PATH")
        or shutil.which("soffice")
        or "/usr/local/bin/soffice"
    )
    cmd = [
        soffice,
        "--headless",
        "--convert-to",
        "pdf",
        "--outdir",
        str(work),
        str(input_file),
    ]
    env = os.environ.copy()
    # Profil LibreOffice isolé par conversion (évite les collisions en parallèle).
    env["HOME"] = str(work)

    result = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        logger.error(
            "Conversion LibreOffice échouée (%s) : %s",
            input_file.name,
            result.stderr,
        )
        raise RuntimeError(f"Conversion LibreOffice échouée pour {input_file.name}")

    generated = work / f"{input_file.stem}.pdf"
    if not generated.exists():
        raise FileNotFoundError(f"PDF converti introuvable : {generated}")

    shutil.copy(generated, output_pdf)
    return str(output_pdf), pdf_page_count(str(output_pdf))


def pdf_page_count(pdf_path: str) -> int:
    """Nombre de pages d'un PDF (via PyMuPDF). 0 si illisible."""
    try:
        import fitz

        with fitz.open(pdf_path) as doc:
            return doc.page_count
    except Exception:
        logger.exception("Comptage de pages impossible pour %s", pdf_path)
        return 0


# ── Routage ─────────────────────────────────────────────────────────────────
def convert_to_pdf(filepath: str, out_dir: str) -> tuple[str, int, str]:
    """Convertit un fichier vers PDF selon son type. Renvoie (pdf, pages, kind).

    - PDF en entrée : pas de conversion, on renvoie le fichier tel quel.
    - texte / image / office : convertisseur dédié.
    - inconnu : ValueError (l'appelant décide quoi faire — souvent DISCARDED).

    La conversion Office est lourde (LibreOffice) : l'appelant la route vers une
    queue dédiée. Ici on se contente de l'exécuter si demandée.
    """
    kind = detect_kind(filepath)
    if kind == KIND_PDF:
        return filepath, pdf_page_count(filepath), KIND_PDF
    if kind == KIND_TEXT:
        pdf, pages = convert_text(filepath, out_dir)
        return pdf, pages, KIND_TEXT
    if kind == KIND_IMAGE:
        pdf, pages = convert_image(filepath, out_dir)
        return pdf, pages, KIND_IMAGE
    if kind == KIND_OFFICE:
        pdf, pages = convert_office(filepath, out_dir)
        return pdf, pages, KIND_OFFICE
    raise ValueError(f"Type de fichier non convertible : {filepath}")
