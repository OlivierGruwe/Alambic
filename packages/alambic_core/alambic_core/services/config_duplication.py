"""alambic_core.services.config_duplication — duplication d'une configuration.

Crée une nouvelle config sur la base d'une existante. La copie :
  - reçoit un nom dérivé de l'original avec un index (« Ma config (copie) »,
    puis « Ma config (copie 2) », etc. pour éviter les collisions) ;
  - naît INACTIVE (is_active=False) : on la règle avant de l'activer, pour
    qu'aucune transaction ne tombe dessus tant qu'elle n'est pas prête ;
  - copie tous les champs métier, y compris les blocs JSONB, les doctypes
    attendus, et les secrets chiffrés (déjà chiffrés au repos, copiés tels quels).

Ne copie PAS : l'id (régénéré), les timestamps (gérés par l'ORM), ni les
transactions/fichiers liés (une config est un modèle, pas ses données).
"""

from __future__ import annotations

import re

from ..db.session import session_scope
from ..models import Config

# Champs métier à copier (tout sauf id/timestamps/relations).
_COPIED_FIELDS = (
    "account_id",
    "need_validation",
    "multi_doc_detect",
    "expected_doctypes",
    "general",
    "edenai_settings",
    "ws",
    "ftp_in_enc",
    "ftp_out_enc",
    "aws_in_enc",
    "aws_out_enc",
    "flower_enc",
    "edenai_secret_enc",
)

_COPY_SUFFIX = re.compile(r"^(.*?)\s*\(copie(?:\s+(\d+))?\)\s*$")


def _next_copy_name(base_name: str, existing_names: set[str]) -> str:
    """Génère un nom de copie unique : « X (copie) », « X (copie 2) », …

    Si base_name est déjà une copie (« X (copie) »), repart de la racine « X »
    pour éviter « X (copie) (copie) ».
    """
    m = _COPY_SUFFIX.match(base_name or "")
    root = m.group(1) if m else (base_name or "")
    root = root.strip() or "config"

    candidate = f"{root} (copie)"
    if candidate not in existing_names:
        return candidate
    i = 2
    while f"{root} (copie {i})" in existing_names:
        i += 1
    return f"{root} (copie {i})"


def duplicate_config(config_id: str) -> str | None:
    """Duplique la config donnée. Renvoie l'id de la nouvelle config, ou None.

    La copie est inactive et porte un nom indexé unique.
    """
    with session_scope() as s:
        src = s.get(Config, config_id)
        if src is None:
            return None

        # Noms existants pour générer un nom de copie unique.
        existing = {name for (name,) in s.query(Config.config_name).all() if name is not None}
        new_name = _next_copy_name(src.config_name or "", existing)

        dst = Config(config_name=new_name, is_active=False)
        for field in _COPIED_FIELDS:
            setattr(dst, field, getattr(src, field))

        s.add(dst)
        s.flush()  # pour récupérer l'id généré
        new_id = dst.id

    return new_id
