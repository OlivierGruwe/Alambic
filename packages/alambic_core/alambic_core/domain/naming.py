"""alambic_core.domain.naming — normalisation des noms en snake_case.

Tous les noms d'entités (compte, configuration, doctype) et les noms de champs
sont stockés en snake_case : minuscules, sans accent, espaces et séparateurs
remplacés par des underscores. Garantit des identifiants cohérents et sûrs à
l'export, dans les comparaisons et dans les URLs.
"""

from __future__ import annotations

import re
import unicodedata


def to_snake_case(value: str) -> str:
    """Normalise une chaîne en snake_case.

    Règles appliquées dans l'ordre :
      1. suppression des accents (é→e, ç→c, à→a…) via décomposition Unicode ;
      2. passage en minuscules ;
      3. tout caractère non alphanumérique devient un underscore ;
      4. compression des underscores multiples et rognage en bord.

    Exemples :
      "Dossier Arondor 2026" → "dossier_arondor_2026"
      "Carte Nationale d'Identité" → "carte_nationale_d_identite"
      "  Façade   N°2 " → "facade_n_2"
      "déjà_ok" → "deja_ok"

    Renvoie "" pour une entrée vide ou faite uniquement de séparateurs.
    """
    if not value:
        return ""

    # 1. Décomposition Unicode puis retrait des marques diacritiques (accents).
    decomposed = unicodedata.normalize("NFKD", str(value))
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))

    # 2-3. Minuscules + tout ce qui n'est pas [a-z0-9] → underscore.
    lowered = without_accents.lower()
    underscored = re.sub(r"[^a-z0-9]+", "_", lowered)

    # 4. Rogner les underscores de bord (les multiples sont déjà compressés).
    return underscored.strip("_")
