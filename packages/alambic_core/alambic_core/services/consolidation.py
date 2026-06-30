"""alambic_core.services.consolidation — orchestration de l'enrichissement.

Après extraction, chaque définition de WS de consolidation (Config.consolidation_ws)
cible UN champ d'un doctype, désigné par `target_field` au format "@doctype:champ"
(ex. "@facture:no_facture"). Si le document courant est de ce doctype et que le
champ a une valeur extraite, on appelle le WS et on enrichit le document.

Tolérance aux pannes : par défaut on_failure="skip" → un WS HS n'interrompt PAS
le pipeline, mais un message est tracé (logger + index de statut ERROR). Si la
définition porte on_failure="error", l'appelant peut choisir de bloquer.

Tout le paramétrage vit dans la config : la définition WS porte sa cible. Les
doctypes ne référencent rien.
"""

from __future__ import annotations

import logging

from .consolidation_client import call_consolidation_ws
from .consolidation_ws import normalize_ws_definition

logger = logging.getLogger(__name__)


def parse_target_field(target: str) -> tuple[str, str]:
    """Parse "@doctype:champ" → (doctype, champ). ("", "") si invalide.

    Tolère l'absence de '@' initial. Le doctype et le champ sont strippés.
    """
    if not target:
        return "", ""
    t = str(target).strip()
    if t.startswith("@"):
        t = t[1:]
    if ":" not in t:
        return "", ""
    doctype, _, field = t.partition(":")
    return doctype.strip(), field.strip()


def enrich_indexes(
    *,
    extracted_indexes: list[dict],
    doctype: str,
    ws_definitions: list[dict],
    allowed_domains=None,
) -> tuple[list[dict], list[str]]:
    """Enrichit les index extraits via les WS ciblant le doctype courant.

    Args:
      extracted_indexes : index extraits [{index_name, index_value, ...}].
      doctype : nom du doctype du document courant (document.doctype).
      ws_definitions : définitions de WS de la config (Config.consolidation_ws).
        Chaque définition porte target_field = "@doctype:champ".
      allowed_domains : allowlist anti-SSRF.

    Returns:
      (nouveaux_index, messages) : index d'enrichissement à ajouter (data du WS +
      un index de statut par champ consolidé) et avertissements (WS sautés).

    Lève RuntimeError uniquement si un WS en on_failure="error" échoue.
    """
    if not ws_definitions or not doctype:
        return [], []

    # Valeur extraite par nom de champ.
    value_by_name = {
        idx.get("index_name"): idx.get("index_value")
        for idx in (extracted_indexes or [])
        if idx.get("index_name")
    }

    new_indexes: list[dict] = []
    messages: list[str] = []

    for ws_def in ws_definitions:
        target = (ws_def or {}).get("target_field") or ""
        ws_doctype, field_name = parse_target_field(target)
        # Le WS ne s'applique qu'aux documents du doctype ciblé.
        if not ws_doctype or ws_doctype != doctype:
            continue
        if not field_name or field_name not in value_by_name:
            continue

        ws = normalize_ws_definition(ws_def)
        ws_name = ws.get("name") or target
        value = value_by_name[field_name]
        result = call_consolidation_ws(ws_def, value, allowed_domains=allowed_domains)

        # Index de statut de validation pour ce champ.
        new_indexes.append(
            {
                "index_name": f"{field_name}__consolidation_status",
                "index_value": result.get("status", "UNKNOWN"),
                "index_type": "metadata",
            }
        )

        if not result.get("ok"):
            msg = f"WS '{ws_name}' pour '{field_name}' : {result.get('error')}"
            if ws.get("on_failure") == "error":
                logger.error("Consolidation bloquante échouée — %s", msg)
                raise RuntimeError(msg)
            logger.warning("Consolidation sautée (non bloquant) — %s", msg)
            messages.append(msg)
            continue

        # Enrichissement : data du WS → nouveaux index metadata.
        for name, val in (result.get("data") or {}).items():
            new_indexes.append(
                {"index_name": name, "index_value": val, "index_type": "metadata"}
            )

    return new_indexes, messages
