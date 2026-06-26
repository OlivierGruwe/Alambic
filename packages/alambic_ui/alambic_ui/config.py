"""Configuration de l'application Flask alambic_ui."""

from __future__ import annotations

import os


class Config:
    """Config lue depuis l'environnement (12-factor)."""

    # Clé de signature des sessions Flask (≠ ALAMBIC_SECRET_KEY qui chiffre les
    # secrets métier). À définir en prod ; valeur de dev par défaut sinon.
    SECRET_KEY = os.environ.get("ALAMBIC_UI_SECRET_KEY", "dev-only-change-me")

    # Sécurité des cookies de session.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Passe à True derrière HTTPS (prod).
    SESSION_COOKIE_SECURE = os.environ.get("ALAMBIC_UI_HTTPS", "").lower() == "true"

    WTF_CSRF_ENABLED = True
