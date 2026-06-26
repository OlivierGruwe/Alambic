"""alambic_core.ai.ocr_parsing — extraction des lignes positionnées par provider.

Porté de FlowerScan (fcl_edenai_ocr, helpers de parsing). Les vraies positions
par ligne sont dans `original_response`, mais leur format dépend du provider.
On reconstruit une liste normalisée :

    [{"text": str, "page": int, "bbox": {"x0","y0","x1","y1"}}]  coords en % (0..100)

Textract est implémenté. Mistral et Google sont des placeholders (retournent []
→ déclenche le fallback contenu, pas de régression) à compléter sur échantillon
réel.
"""

from __future__ import annotations

import re


def _find_key(obj, target: str, _depth: int = 0):
    """Première valeur associée à `target` en descente récursive (dict/list).

    EdenAI encapsule parfois la réponse native sous une clé provider, d'où la
    recherche en profondeur (bornée).
    """
    if _depth > 6 or obj is None:
        return None
    if isinstance(obj, dict):
        if target in obj:
            return obj[target]
        for v in obj.values():
            found = _find_key(v, target, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key(v, target, _depth + 1)
            if found is not None:
                return found
    return None


def _textract_lines(original: dict) -> list:
    """Amazon Textract : blocs LINE avec BoundingBox (Left/Top/Width/Height 0..1)."""
    blocks = _find_key(original, "Blocks")
    if not isinstance(blocks, list):
        return []

    out = []
    for b in blocks:
        if not isinstance(b, dict) or b.get("BlockType") != "LINE":
            continue
        text = (b.get("Text") or "").strip()
        if not text:
            continue
        geo = (b.get("Geometry") or {}).get("BoundingBox") or {}
        try:
            left = float(geo.get("Left", 0.0))
            top = float(geo.get("Top", 0.0))
            width = float(geo.get("Width", 0.0))
            height = float(geo.get("Height", 0.0))
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "text": text,
                "page": int(b.get("Page", 1) or 1),
                "bbox": {
                    "x0": round(left * 100.0, 4),
                    "y0": round(top * 100.0, 4),
                    "x1": round((left + width) * 100.0, 4),
                    "y1": round((top + height) * 100.0, 4),
                },
            }
        )
    return out


_IMG_MD_PATTERN = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _strip_images(markdown: str) -> str:
    """Retire les références d'images ![alt](src) du markdown (bruit pour la
    classification/extraction de texte)."""
    cleaned = _IMG_MD_PATTERN.sub("", markdown)
    # Compacte les lignes vides laissées par les images retirées.
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def mistral_pages_markdown(original: dict) -> list[dict]:
    """Markdown par page depuis une réponse Mistral OCR (via EdenAI).

    Mistral ne fournit PAS de positions par ligne de texte (champ blocks=null),
    mais un markdown structuré par page (titres, paragraphes) + les dimensions.
    On renvoie [{"page": int, "markdown": str}] (images retirées). Les positions
    de lignes ne sont pas disponibles avec Mistral via EdenAI : c'est une
    limitation du provider, pas un parseur à compléter.
    """
    pages = original.get("pages") if isinstance(original, dict) else None
    if not isinstance(pages, list):
        return []
    out = []
    for p in pages:
        if not isinstance(p, dict):
            continue
        md = _strip_images(p.get("markdown") or "")
        if not md:
            continue
        out.append({"page": int(p.get("index", 0)) + 1, "markdown": md})
    return out


def _mistral_lines(original: dict) -> list:
    """Mistral OCR : pas de positions par ligne (blocks=null via EdenAI).

    On retourne [] pour les LIGNES POSITIONNÉES (le markdown structuré est
    récupéré séparément via mistral_pages_markdown). Ce n'est pas un placeholder
    à compléter : Mistral via EdenAI ne fournit tout simplement pas la géométrie
    par ligne.
    """
    return []


def _google_lines(original: dict) -> list:
    """Google Document AI : placeholder. [] → fallback."""
    return []


# Dispatch par provider. Clé matchée en 'in' (tolérant aux suffixes EdenAI type
# 'amazon/ocr', 'ocr/ocr/amazon', 'textract').
_LINE_PARSERS = {
    "amazon": _textract_lines,
    "textract": _textract_lines,
    "mistral": _mistral_lines,
    "google": _google_lines,
}


def parse_positioned_lines(original_response, provider: str) -> list:
    """Lignes positionnées depuis original_response pour le provider donné.

    [] si provider inconnu ou structure non reconnue (le pipeline retombe alors
    sur le texte plat).
    """
    if not original_response:
        return []
    prov = (provider or "").lower()
    parser = None
    for key, fn in _LINE_PARSERS.items():
        if key in prov:
            parser = fn
            break
    if parser is None:
        return []
    try:
        return parser(original_response)
    except Exception:  # noqa: BLE001
        return []
