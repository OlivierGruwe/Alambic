"""alambic_core.mail.parsing — filtrage des expéditeurs et extraction d'en-têtes.

Le découpage corps/pièces jointes et le filtrage par content_mode / extensions
sont déjà assurés par le pipeline d'extraction (EmlProcessor + MailContentPolicy).
Ce module ne couvre donc QUE ce qui manque en amont : décider si un mail doit être
ingéré selon la whitelist d'expéditeurs, et lire l'expéditeur d'un message brut.
"""

from __future__ import annotations

import email
import fnmatch
import re
from dataclasses import dataclass


@dataclass
class SenderFilter:
    """Whitelist d'expéditeurs (wildcards * et ?). Vide = tous acceptés."""

    sender_whitelist: str = ""

    def allowed(self, sender: str) -> bool:
        """True si l'expéditeur passe la whitelist.

        Extrait l'adresse depuis « Prénom Nom <email@domaine> » si nécessaire,
        compare en minuscules avec chaque motif (fnmatch : * et ?).
        """
        wl = (self.sender_whitelist or "").strip()
        if not wl:
            return True
        match = re.search(r"<([^>]+)>", sender or "")
        addr = match.group(1).lower() if match else (sender or "").lower().strip()
        patterns = [p.strip().lower() for p in wl.replace("\n", ",").split(",") if p.strip()]
        return any(fnmatch.fnmatch(addr, p) for p in patterns)


def sender_of(raw: bytes) -> str:
    """Lit l'en-tête From d'un message RFC822 brut (chaîne vide si absent)."""
    try:
        msg = email.message_from_bytes(raw)
    except (ValueError, TypeError):
        return ""
    return msg.get("From", "") or ""


def sender_filter_from_config(mail_config) -> SenderFilter:
    """Construit le filtre d'expéditeurs depuis une MailConfig."""
    return SenderFilter(sender_whitelist=mail_config.sender_whitelist or "")
