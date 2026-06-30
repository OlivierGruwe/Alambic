"""alambic_core.services.config_fields — champs propagés à la transaction.

Porté de FlowerScan (core.fcl_config_fields). Des champs déclarés au niveau de la
CONFIG dont la valeur est résolue une fois par transaction et propagée comme
index metadata sur CHAQUE document de la transaction.

À la différence des champs de doctype (extraits du CONTENU de chaque document),
les champs propagés viennent de la transaction et sont communs à tous ses
documents. Deux familles :

  - source_type="context"  : valeur issue de la SOURCE du dépôt (email, import).
      source_key = clé de métadonnée source (ex. "from", "subject", "date").
      La valeur existe déjà dans le contexte fourni à l'ingestion.

  - source_type="computed" : valeur GÉNÉRÉE au traitement, via un token.
      source_key = token parmi ceux listés ci-dessous (ex. "@today", "@now").

Structure d'un champ propagé (volontairement plus simple qu'un field doctype) :
    {
      "field_name":    "email_from",     # nom de l'index produit (obligatoire)
      "field_label":   "Expéditeur",     # libellé UI (optionnel)
      "source_type":   "context" | "computed",
      "source_key":    "from" | "@today",
      "default_value": ""                # repli si la source est absente
    }
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Paris")


def _now() -> str:
    return datetime.now(_TZ).strftime("%d/%m/%Y %H:%M:%S")


def _today() -> str:
    return datetime.now(_TZ).strftime("%Y_%m_%d")


def _year_month() -> str:
    d = datetime.now(_TZ)
    return f"{d.year}-{d.month:02d}"


def _is_empty(v) -> bool:
    return v is None or str(v).strip() == ""


# Tokens calculés autonomes : callable sans argument.
_COMPUTED_TOKENS = {
    "@today": _today,  # 2026_06_30
    "@now": _now,  # 30/06/2026 14:25:43
    "@datetime": _now,  # alias de @now
    "@year_month": _year_month,  # 2026-06
}

# Tokens calculés dont la valeur vient du contexte d'exécution.
# token → clé à lire dans le dict context fourni par l'appelant.
_COMPUTED_CONTEXT_TOKENS = {
    "@transaction_id": "transaction_id",
    "@config_name": "config_name",
}


def computed_tokens() -> list[str]:
    """Liste des tokens calculés disponibles (pour l'UI / la doc)."""
    return sorted([*_COMPUTED_TOKENS.keys(), *_COMPUTED_CONTEXT_TOKENS.keys()])


def _resolve_computed(source_key: str, default_value: str, context: dict) -> str:
    """Résout un token calculé. Token inconnu → default_value."""
    key = (source_key or "").strip().lower()

    # Token contextuel (transaction_id, config_name) : lu dans context.
    ctx_key = _COMPUTED_CONTEXT_TOKENS.get(key)
    if ctx_key is not None:
        val = (context or {}).get(ctx_key)
        return str(val) if not _is_empty(val) else (default_value or "")

    # Token autonome (date/heure).
    fn = _COMPUTED_TOKENS.get(key)
    if fn is None:
        return default_value or ""
    try:
        return str(fn())
    except Exception:  # noqa: BLE001
        return default_value or ""


def _resolve_context(source_key: str, context: dict, default_value: str) -> str:
    """Résout un champ contexte depuis les métadonnées source (casse tolérée)."""
    if not context:
        return default_value or ""
    key = (source_key or "").strip()
    if key in context and not _is_empty(context[key]):
        return str(context[key])
    lk = key.lower()
    for k, v in context.items():
        if str(k).lower() == lk and not _is_empty(v):
            return str(v)
    return default_value or ""


def resolve_config_field(field: dict, context: dict | None = None) -> str:
    """Résout la valeur d'UN champ propagé."""
    source_type = (field.get("source_type") or "").strip().lower()
    source_key = field.get("source_key") or ""
    default = field.get("default_value") or ""

    if source_type == "computed":
        return _resolve_computed(source_key, default, context or {})
    if source_type == "context":
        return _resolve_context(source_key, context or {}, default)
    return default  # type inconnu → default (robustesse)


def resolve_config_fields(fields: list[dict], context: dict | None = None) -> list[dict]:
    """Résout TOUS les champs propagés d'une config.

    Retourne une liste prête à indexer :
        [{"name": field_name, "value": <résolu>, "label": field_label}, ...]
    Les champs sans field_name sont ignorés. Une valeur vide n'est PAS filtrée
    ici (un default_value peut être intentionnellement vide) — c'est à l'appelant
    de décider s'il indexe les valeurs vides.
    """
    out = []
    for f in fields or []:
        name = (f.get("field_name") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "value": resolve_config_field(f, context),
                "label": (f.get("field_label") or "").strip(),
            }
        )
    return out
