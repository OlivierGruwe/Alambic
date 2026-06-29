"""
alambic_ui.doctype_schema — sérialisation des doctypes.

Le json_content d'un doctype est une chaîne JSON : {"document_type": str,
"fields": [ {19 attributs}, ... ]}. Ce module fait le pont entre cette
représentation stockée et le formulaire d'édition (parse / build), en restant
fidèle au format d'origine (booléens stockés en 0/1, attributs absents → "").
"""

from __future__ import annotations

import json

# Spécification des attributs d'un champ : (clé, libellé, catégorie, widget, options).
# Catégorie "essentiel" = toujours visible ; "avance" = replié.
_BCR_TYPES = [
    "",
    "Aztec",
    "Codabar",
    "Code39",
    "Code93",
    "Code128",
    "DataMatrix",
    "EAN8",
    "EAN13",
    "ITF",
    "MaxiCode",
    "PDF417",
    "QRCode",
    "MicroQRCode",
    "RMQRCode",
    "DataBar",
    "DataBarExpanded",
    "DataBarLimited",
    "DXFilmEdge",
    "UPCA",
]

# N'inclut QUE les attributs réellement lus par le moteur d'extraction
# (fcl_field_extractor) ou utiles à l'opérateur. Les attributs morts du format
# historique (field_format, block_search, priority, is_hidden) ont été retirés.
FIELD_SPEC: list[tuple[str, str, str, str, list[str] | None]] = [
    ("field_name", "Nom du champ", "essentiel", "text", None),
    (
        "field_type",
        "Type",
        "essentiel",
        "select",
        ["string", "number", "date", "float", "object", "array"],
    ),
    ("field_description", "Description", "essentiel", "textarea", None),
    ("required", "Requis", "essentiel", "checkbox", None),
    ("use_ia", "Utiliser l'IA", "essentiel", "checkbox", None),
    ("is_separator", "Séparateur", "essentiel", "checkbox", None),
    ("regexp", "Expression régulière", "avance", "text", None),
    ("anchors", "Ancres", "avance", "text", None),
    (
        "direction",
        "Direction",
        "avance",
        "select",
        ["right", "left", "below", "above", "block", "any"],
    ),
    ("max_distance", "Distance max", "avance", "text", None),
    ("page_zone", "Zone de page", "avance", "text", None),
    ("bcr_type", "Code-barres", "avance", "select", _BCR_TYPES),
    ("default_value", "Valeur par défaut", "avance", "text", None),
    ("black_words", "Mots exclus", "avance", "text", None),
    ("strategy", "Stratégie", "avance", "select", ["", "mrz"]),
]

# Attributs stockés en 0/1 dans le JSON, présentés comme cases à cocher.
BOOL_KEYS = {"required", "is_separator", "use_ia"}

_KEYS = [k for k, _, _, _, _ in FIELD_SPEC]
_ESSENTIAL = [k for k, _, cat, _, _ in FIELD_SPEC if cat == "essentiel"]
_ADVANCED = [k for k, _, cat, _, _ in FIELD_SPEC if cat == "avance"]


def parse_doctype(json_content: str) -> dict:
    """Parse le json_content stocké en structure éditable.

    Renvoie {"document_type": str, "fields": [ {clé: valeur} ]} où les booléens
    0/1 deviennent des bool Python. Tolère un contenu vide ou invalide (→ vide).
    """
    if not json_content or not json_content.strip():
        return {"document_type": "", "fields": []}
    try:
        data = json.loads(json_content)
    except (ValueError, TypeError):
        return {"document_type": "", "fields": []}

    fields = []
    for raw in data.get("fields", []):
        field = {}
        for key in _KEYS:
            val = raw.get(key, "")
            if key in BOOL_KEYS:
                field[key] = _to_bool(val)
            else:
                field[key] = "" if val is None else str(val)
        fields.append(field)
    return {"document_type": data.get("document_type", ""), "fields": fields}


def build_json_content(document_type: str, fields: list[dict]) -> str:
    """Reconstruit le json_content à partir des champs édités.

    Fidèle au format d'origine : booléens reconvertis en 0/1, tous les attributs
    présents (même vides), ordre de la spec respecté.
    """
    out_fields = []
    for f in fields:
        out = {}
        for key in _KEYS:
            if key in BOOL_KEYS:
                out[key] = 1 if _to_bool(f.get(key)) else 0
            else:
                out[key] = (f.get(key) or "").strip()
        out_fields.append(out)
    return json.dumps(
        {"document_type": document_type, "fields": out_fields},
        ensure_ascii=False,
        indent=2,
    )


def _to_bool(val) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, int):
        return val == 1
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "on", "yes")
    return False


def empty_field() -> dict:
    """Un champ vierge (pour l'ajout d'un nouveau champ)."""
    return {k: (False if k in BOOL_KEYS else "") for k in _KEYS}
