"""alambic_core.services.barcode_gating — décision de lecture des codes-barres.

Règle (portée de FlowerScan cab_extract.handler, version pragmatique) : on lit
les codes-barres d'un document si AU MOINS UN champ du doctype de sa config a un
`bcr_type` non vide. Sinon, lire le CAB serait du gaspillage (rendu image +
scan, 200-2000 ms/doc).

Le doctype porte ses champs dans `json_content` (JSON base64 en CSV d'import, mais
stocké décodé en base). Structure : {"document_type": ..., "fields": [{...,
"bcr_type": "Code128"|"", ...}]}.

La ré-évaluation post-classification (mode AUTO de FlowerScan, où le doctype
n'est connu qu'après le classifier) sera rebranchée quand la brique de
classification existera.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


def _parse_fields(json_content: str) -> list[dict]:
    """Extrait la liste des champs d'un doctype depuis son json_content.

    Tolérant : json_content peut être du JSON brut. Renvoie [] si illisible.
    """
    if not json_content:
        return []
    try:
        data = json.loads(json_content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Doctype json_content illisible (JSON invalide)")
        return []
    fields = data.get("fields")
    return fields if isinstance(fields, list) else []


def doctype_needs_cab(json_content: str) -> bool:
    """True si un champ du doctype a un bcr_type non vide (→ lire les barcodes)."""
    for field in _parse_fields(json_content):
        bcr = field.get("bcr_type", "") if isinstance(field, dict) else ""
        if bcr and str(bcr).strip():
            return True
    return False
