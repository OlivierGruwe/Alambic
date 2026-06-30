"""Régression : le découpage par code-barres (readCAB) sur document multi-pages.

Reproduit le cas réel : un document de 10 pages avec un code-barres de valeur
différente toutes les 2 pages → 5 documents logiques. Le bug d'origine : les
barcodes produits par readCAB (liste à plat avec n° de page) n'étaient pas
rangés par page, donc detect_multidocument ne voyait aucun code-barres.
"""

from __future__ import annotations

from alambic_core.services.doc_splitting import split_pages

from alambic_workers.tasks.split import _inject_barcodes, _pages_by_number


def test_pages_by_number_guarantees_barcodes_key():
    pages = _pages_by_number([{"page": 1, "lines": [], "markdown": ""}])
    assert pages[1]["barcodes"] == []


def test_inject_barcodes_groups_by_page():
    pages = _pages_by_number([{"page": i, "lines": []} for i in range(1, 4)])
    barcodes = [
        {"value": "A", "page": 1, "format": "DataMatrix"},
        {"value": "B", "page": 3, "format": "DataMatrix"},
    ]
    _inject_barcodes(pages, barcodes)
    assert len(pages[1]["barcodes"]) == 1
    assert pages[2]["barcodes"] == []
    assert pages[3]["barcodes"][0]["value"] == "B"


def test_split_10_pages_5_documents():
    """10 pages, code-barres distinct toutes les 2 pages → 5 documents."""
    ocr_pages = [{"page": i, "lines": [], "markdown": ""} for i in range(1, 11)]
    pages = _pages_by_number(ocr_pages)
    barcodes = []
    for i, page in enumerate((1, 3, 5, 7, 9)):
        barcodes.append({"value": f"DOC{i}-DM", "page": page, "format": "DataMatrix"})
        barcodes.append({"value": f"DOC{i}-128", "page": page, "format": "Code128"})
    _inject_barcodes(pages, barcodes)

    groups = split_pages(pages)
    assert len(groups) == 5
    assert groups == [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10]]


def test_child_ocr_renumbered_from_parent():
    """L'OCR de l'enfant reprend la tranche du parent, pages renumérotées à 1."""
    import base64

    from alambic_workers.tasks.split import _child_ocr_from_parent

    def b64(s):
        return base64.b64encode(s.encode()).decode()

    ocr_pages = [
        {"page": i, "lines": [{"text": b64(f"Texte {i}"), "position": {}}], "markdown": ""}
        for i in range(1, 7)
    ]
    pages = _pages_by_number(ocr_pages)
    _inject_barcodes(pages, [{"value": "X", "page": 3, "format": "DataMatrix"}])

    # Enfant = pages 3-4 du parent.
    child_lines, child_md = _child_ocr_from_parent(pages, [3, 4])
    assert child_lines[0]["page"] == 1  # page 3 → 1
    assert child_lines[1]["page"] == 2  # page 4 → 2
    assert child_lines[0]["barcodes"][0]["value"] == "X"
    assert "[PAGE 1]" in child_md
    assert "Texte 3" in child_md
    assert "[PAGE 3]" not in child_md  # renuméroté
