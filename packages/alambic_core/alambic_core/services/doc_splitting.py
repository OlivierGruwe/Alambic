"""alambic_core.services.doc_splitting — découpage logique d'un document multi-pages.

Porté de FlowerScan (doc_splitter). Décide où un fichier multi-pages se scinde en
plusieurs documents logiques, selon 3 stratégies (priorité décroissante) :

1. fixed_page : coupe tous les N pages (si configuré).
2. separator  : coupe quand un champ `is_separator` change de valeur d'une page
   à l'autre (ex. un numéro de dossier). Utilise field_extractor.
3. multi-doc  : coupe sur signaux PHYSIQUES (pas de classification) —
   changement de code-barres (valeur ou position/type), ou reset de pagination
   (retour à "page 1"). Utilise les barcodes (readCAB) et le texte OCR.

Entrée : `pages` = {page_num: {"lines": [...], "barcodes": [...]}} (1-indexé),
issu de PdfExtractor.to_json() réindexé par page.
Sortie : liste de groupes de pages [[1,2], [3], [4,5,6]], chaque groupe = un
document logique. La création physique (documents-enfants via parent_id) est
faite par la tâche detect_split.
"""

from __future__ import annotations

import base64
import re

# [PAGE N] (notre format), "page N", et "N / M".
PAGE_TAG_RE = re.compile(r"\[PAGE\s+(\d+)\]")
PAGE_WORD_RE = re.compile(r"page\s+(\d+)", re.I)


def _decode_text(data: str) -> str:
    try:
        return base64.b64decode(data).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _page_text(page: dict) -> str:
    parts = []
    for line in page.get("lines", []):
        txt = _decode_text(line.get("text", ""))
        if txt:
            parts.append(txt)
    return "\n".join(parts)


def _extract_page_number(page_text: str) -> int | None:
    """Numéro de page lu dans le texte ([PAGE N] est notre marqueur interne, on
    l'ignore ; on cherche une pagination *du document* type 'page N')."""
    m = PAGE_WORD_RE.search(page_text)
    if m:
        return int(m.group(1))
    return None


def _barcode_zone(pos: dict) -> str:
    x = (pos.get("x0", 0) + pos.get("x1", 0)) / 2
    y = (pos.get("y0", 0) + pos.get("y1", 0)) / 2
    vertical = "top" if y < 20 else ("middle" if y < 70 else "bottom")
    horizontal = "left" if x < 33 else ("center" if x < 66 else "right")
    return f"{vertical}-{horizontal}"


def _barcode_signature(bc: dict) -> str:
    return f"{bc.get('format')}@{_barcode_zone(bc.get('position', {}))}"


def detect_multidocument(pages: dict) -> set[int]:
    """Pages où commence un nouveau document, par signaux physiques.

    Renvoie l'ensemble des numéros de page qui débutent un nouveau document
    (changement de code-barres ou reset de pagination).
    """
    cuts: set[int] = set()
    last_bc_value = None
    last_bc_sig = None
    last_page_number = None

    for p in sorted(pages.keys()):
        page = pages[p]

        # Signal code-barres.
        barcodes = page.get("barcodes", []) or []
        if barcodes:
            bc = barcodes[0]
            bc_value = bc.get("value")
            bc_sig = _barcode_signature(bc)
            if (
                bc_value
                and last_bc_value
                and bc_value != last_bc_value
                or bc_sig
                and last_bc_sig
                and bc_sig != last_bc_sig
            ):
                cuts.add(p)
            last_bc_value = bc_value
            last_bc_sig = bc_sig

        # Signal reset de pagination.
        page_number = _extract_page_number(_page_text(page))
        if page_number == 1 and last_page_number not in (None, 0):
            cuts.add(p)
        if page_number is not None:
            last_page_number = page_number

    return cuts


def _separator_value(page: dict, separator_fields: list[dict]):
    """Valeur du premier champ séparateur trouvé sur la page (via field_extractor)."""
    from alambic_core.ai.field_extractor import extract_field

    for field in separator_fields:
        value = extract_field(page, field)
        if value:
            return value
    return None


def split_pages(
    pages: dict, *, fields: list[dict] | None = None, fixed_page: int = 0
) -> list[list[int]]:
    """Découpe les pages en groupes (un groupe = un document logique).

    Priorité : fixed_page > separator (champs is_separator) > multi-doc physique.
    """
    fields = fields or []
    page_numbers = sorted(pages.keys())
    if not page_numbers:
        return []

    # Mode fixed_page : coupe tous les N pages.
    if fixed_page and fixed_page > 0:
        groups = []
        current: list[int] = []
        for p in page_numbers:
            if len(current) == fixed_page:
                groups.append(current)
                current = []
            current.append(p)
        if current:
            groups.append(current)
        return groups

    separator_fields = [
        f
        for f in fields
        if str(f.get("is_separator", "0")).strip().lower() in ("1", "true", "on", "yes")
    ]

    # Coupes physiques (utilisées seulement si pas de séparateur métier).
    cuts = set() if separator_fields else detect_multidocument(pages)

    groups = []
    current = []
    last_sep_value = None

    for p in page_numbers:
        page = pages[p]

        if separator_fields:
            # Mode separator : coupe au changement de valeur du séparateur.
            sep_value = _separator_value(page, separator_fields)
            new_doc = sep_value is not None and (
                last_sep_value is None or sep_value != last_sep_value
            )
            if new_doc:
                if current:
                    groups.append(current)
                current = [p]
                last_sep_value = sep_value
            else:
                current.append(p)
        else:
            # Mode multi-doc : coupe sur signaux physiques.
            if p in cuts and current:
                groups.append(current)
                current = []
            current.append(p)

    if current:
        groups.append(current)
    return groups
