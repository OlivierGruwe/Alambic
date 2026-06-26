"""alambic_workers.barcode — lecture des codes-barres d'un PDF (readCAB).

Porté de FlowerScan (cab_extract.__get_cab__). On rend chaque page du PDF en
image puis on lit les codes-barres avec zxing-cpp.

Optimisations mémoire CONSERVÉES du code d'origine (elles évitaient des
OutOfMemory en Lambda, et protègent aussi le worker cab conteneurisé) :
- DPI adaptatif : on plafonne le nombre de pixels rendus par page
  (MAX_RENDER_PIXELS) ; au-delà, on baisse le DPI effectif.
- Rendu en NIVEAUX DE GRIS (1 canal) au lieu de RGB (3) : divise par 3 la RAM,
  et zxing lit aussi bien en gris.
- Libération page par page (pixmap + image PIL + gc.collect) : sans ça, la RAM
  cumule les buffers de toutes les pages.

Les positions des codes-barres sont normalisées en POURCENTAGES (0-100) de la
page, indépendamment du DPI de rendu — ce qui les rend utilisables par le
découpage (brique F) quel que soit le rendu.
"""

from __future__ import annotations

import gc
import logging
import os

logger = logging.getLogger(__name__)

# Plafond de pixels par page rendue. Au-delà, on baisse le DPI effectif pour
# borner l'empreinte mémoire (rendu d'une page en gris ≈ largeur*hauteur octets).
MAX_RENDER_PIXELS = 24_000_000

# 200 DPI suffit pour lire des codes-barres et divise par ~2.25 la RAM vs 300 DPI
# (mémoire ∝ DPI²). Combiné au rendu en gris, c'est ce qui borne la mémoire.
DEFAULT_RESOLUTION = 200


def _normalize_percent(x0: float, y0: float, x1: float, y1: float, w: int, h: int) -> dict:
    """Position en pourcentages (0-100) de la page, depuis des pixels."""
    if w <= 0 or h <= 0:
        return {}
    return {
        "x0": round(x0 / w * 100, 2),
        "y0": round(y0 / h * 100, 2),
        "x1": round(x1 / w * 100, 2),
        "y1": round(y1 / h * 100, 2),
    }


def read_barcodes(pdf_path: str, resolution: int = DEFAULT_RESOLUTION) -> list[dict]:
    """Lit les codes-barres de toutes les pages d'un PDF.

    Renvoie une liste de dicts {value, page, format, position}. position est en
    pourcentages de la page. Liste vide si le fichier est absent/illisible ou
    sans code-barres (jamais d'exception propagée : la lecture CAB est best-effort).
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        logger.warning("readCAB : fichier introuvable : %s", pdf_path)
        return []

    # Imports tardifs : le worker normal n'a pas besoin de ces libs lourdes.
    try:
        import fitz  # PyMuPDF
        import zxingcpp
        from PIL import Image
    except ImportError as exc:
        logger.error("readCAB : dépendance manquante (%s)", exc)
        return []

    barcodes: list[dict] = []
    try:
        with fitz.open(pdf_path) as pdf:
            for page_index in range(pdf.page_count):
                pix = None
                img = None
                try:
                    page = pdf.load_page(page_index)

                    # DPI adaptatif : on plafonne les pixels rendus.
                    rect = page.rect
                    scale = resolution / 72.0
                    est_px = (rect.width * scale) * (rect.height * scale)
                    if est_px > MAX_RENDER_PIXELS and est_px > 0:
                        scale *= (MAX_RENDER_PIXELS / est_px) ** 0.5

                    matrix = fitz.Matrix(scale, scale)
                    # Rendu en niveaux de gris (1 canal).
                    pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY)
                    img = Image.frombytes("L", (pix.width, pix.height), pix.samples)

                    # Pixmap libéré dès l'image PIL construite.
                    del pix
                    pix = None

                    for bc in zxingcpp.read_barcodes(img):
                        try:
                            pos = bc.position
                            fmt = str(bc.format).split(".")[-1]
                            barcodes.append(
                                {
                                    "value": bc.text,
                                    "page": page_index + 1,
                                    "format": fmt,
                                    "position": _normalize_percent(
                                        pos.top_left.x,
                                        pos.top_left.y,
                                        pos.bottom_right.x,
                                        pos.bottom_right.y,
                                        img.width,
                                        img.height,
                                    ),
                                }
                            )
                        except Exception as ex:  # noqa: BLE001
                            logger.warning(
                                "readCAB : barcode illisible page %s : %s",
                                page_index + 1,
                                ex,
                            )
                except Exception as ex:  # noqa: BLE001
                    logger.warning("readCAB : page %s illisible : %s", page_index + 1, ex)
                finally:
                    # Libération systématique par page (mémoire bornée).
                    if img is not None:
                        try:
                            img.close()
                        except Exception:  # noqa: BLE001, S110
                            pass
                    img = None
                    pix = None
                    gc.collect()
    except Exception as ex:  # noqa: BLE001
        logger.exception("readCAB : échec ouverture PDF %s : %s", pdf_path, ex)
        return barcodes

    logger.info(
        "readCAB : %d code(s)-barres lus dans %s", len(barcodes), os.path.basename(pdf_path)
    )
    return barcodes
