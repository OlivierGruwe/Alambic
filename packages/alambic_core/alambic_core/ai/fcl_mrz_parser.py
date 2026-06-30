"""alambic_core.ai.fcl_mrz_parser — lecture de la MRZ (ICAO 9303).

Porté de FlowerScan (flowerscan_lib.ia.fcl_mrz_parser). Lit la Machine Readable
Zone des documents d'identité (passeport TD3, CNI/titre de séjour TD1, visas et
anciens documents TD2) à partir du texte OCR.

Choix d'architecture :
  - L'OCR (EdenAI ou local) fournit déjà le texte du document, MRZ comprise. On
    ne fait donc PAS de détection d'image ici (pas d'OpenCV/tesseract) : on
    DÉTECTE les lignes MRZ dans le texte OCR, puis on les PARSE et VALIDE avec la
    lib `mrz` (pure Python, checksums ICAO 9303).
  - La validation par checksum est l'atout de la MRZ : elle dit si la lecture est
    fiable. On expose `valid` (tous les checksums OK) pour que l'appelant décide
    de faire confiance à la MRZ ou de retomber sur anchors/zone/IA.

Formats :
  - TD3 (passeport)         : 2 lignes de 44 caractères
  - TD1 (CNI, titre séjour) : 3 lignes de 30 caractères
  - TD2 (anciens, visas)    : 2 lignes de 36 caractères
"""

from __future__ import annotations

import re

# Caractères autorisés dans une ligne MRZ.
_MRZ_CHARSET = re.compile(r"^[A-Z0-9<]+$")

# Longueurs canoniques par format.
_TD1_LEN = 30
_TD2_LEN = 36
_TD3_LEN = 44


def _clean_line(s: str) -> str:
    """Majuscule, espaces retirés, séparateurs normalisés en '<'."""
    if not s:
        return ""
    s = s.upper().strip()
    # L'OCR rend parfois les '<' comme espaces, guillemets, etc. Les lignes MRZ
    # n'ont pas d'espace : on normalise les espaces internes en '<'.
    s = s.replace(" ", "<")
    s = re.sub(r"[«»“”\"'`]", "<", s)
    return s


def _looks_like_mrz_line(s: str) -> bool:
    """Heuristique : la chaîne ressemble-t-elle à une ligne MRZ ?"""
    if not s:
        return False
    s = _clean_line(s)
    if not _MRZ_CHARSET.match(s):
        return False
    # Une vraie ligne MRZ a une longueur proche d'un format connu (~26 char min).
    return len(s) >= _TD1_LEN - 4


def _pad(s: str, length: int) -> str:
    """Ajuste une ligne à la longueur cible (tronque ou complète avec '<')."""
    s = s[:length]
    return s.ljust(length, "<")


def extract_mrz_lines(text_lines: list[str]) -> list[str] | None:
    """Cherche un bloc de lignes MRZ consécutives dans le texte OCR.

    Retourne la liste des lignes MRZ nettoyées (2 ou 3 lignes) ou None. On repère
    les lignes candidates (charset + longueur) et on prend le plus grand bloc de
    longueur homogène proche d'un format connu.
    """
    candidates = []
    for raw in text_lines:
        for sub in str(raw).splitlines():
            c = _clean_line(sub)
            if _looks_like_mrz_line(c):
                candidates.append(c)

    if len(candidates) < 2:
        return None

    # Groupe par longueur dominante proche d'un format connu.
    for target_len, n_lines in ((_TD3_LEN, 2), (_TD1_LEN, 3), (_TD2_LEN, 2)):
        block = [c for c in candidates if abs(len(c) - target_len) <= 3]
        if len(block) >= n_lines:
            # La MRZ est en bas de page : prend les n dernières, ajuste la longueur.
            chosen = block[-n_lines:]
            return [_pad(c, target_len) for c in chosen]

    return None


def parse_mrz(lines: list[str]) -> dict | None:
    """Parse et valide une MRZ (2 ou 3 lignes déjà nettoyées).

    Retourne un dict de champs normalisés + `valid` (tous checksums OK), ou None
    si le format n'est pas reconnu / parsing impossible.

    Clé `valid` :
      True  → tous les chiffres de contrôle concordent (lecture fiable) ;
      False → au moins un checksum KO (OCR douteux), à utiliser avec prudence.
    """
    if not lines or len(lines) < 2:
        return None

    joined = "\n".join(lines)
    checker = None
    doc_format = None

    try:
        if len(lines) == 3:
            from mrz.checker.td1 import TD1CodeChecker

            checker = TD1CodeChecker(joined)
            doc_format = "TD1"
        elif len(lines) == 2 and len(lines[0]) >= _TD3_LEN - 2:
            from mrz.checker.td3 import TD3CodeChecker

            checker = TD3CodeChecker(joined)
            doc_format = "TD3"
        else:
            from mrz.checker.td2 import TD2CodeChecker

            checker = TD2CodeChecker(joined)
            doc_format = "TD2"
    except Exception:  # noqa: BLE001
        return None

    try:
        valid = bool(checker)  # True si tous les checksums sont OK
        f = checker.fields()
    except Exception:  # noqa: BLE001
        return None

    return {
        "format": doc_format,
        "valid": valid,
        "document_type": getattr(f, "document_type", ""),
        "country": getattr(f, "country", ""),
        "document_number": getattr(f, "document_number", ""),
        "surname": getattr(f, "surname", ""),
        "name": getattr(f, "name", ""),
        "nationality": getattr(f, "nationality", ""),
        "birth_date": getattr(f, "birth_date", ""),
        "sex": getattr(f, "sex", ""),
        "expiry_date": getattr(f, "expiry_date", ""),
        "optional_data": getattr(f, "optional_data", ""),
    }


# Correspondance nom de sous-champ MRZ → clé du dict parse_mrz. Sert au
# field_extractor (strategy="mrz", anchors="mrz:document_number", etc.).
MRZ_SUBFIELDS = {
    "document_type",
    "country",
    "document_number",
    "surname",
    "name",
    "nationality",
    "birth_date",
    "sex",
    "expiry_date",
    "optional_data",
}


def parse_mrz_from_text(text_lines: list[str]) -> dict | None:
    """Détecte les lignes MRZ dans un texte OCR puis les parse.

    Retourne le dict de champs (avec `valid`) ou None.
    """
    lines = extract_mrz_lines(text_lines)
    if not lines:
        return None
    return parse_mrz(lines)
