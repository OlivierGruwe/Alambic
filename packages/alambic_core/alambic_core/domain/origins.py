"""alambic_core.domain.origins — canaux d'ingestion d'une transaction.

Une transaction porte une origine (colonne Transaction.origin) qui indique par
quel canal le document est entré : upload manuel, mail, web service, etc. Ce
module centralise les valeurs et leurs libellés lisibles, partagés par l'UI
(dashboard, liste des transactions).
"""

from __future__ import annotations

# Libellés lisibles des origines (code technique → libellé affiché).
ORIGIN_LABELS = {
    "MAIL": "Mail",
    "WS": "Web service",
    "UI_IMPORT": "Upload manuel",
    "FTP": "FTP",
    "S3": "S3",
    "API": "API",
    "UNKNOWN": "Inconnu",
    "": "—",
}


def origin_label(origin: str | None) -> str:
    """Libellé lisible d'une origine ; renvoie le code brut si inconnu."""
    return ORIGIN_LABELS.get(origin or "", origin or "—")
