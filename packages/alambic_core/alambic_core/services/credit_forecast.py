"""alambic_core.services.credit_forecast — projection d'autonomie des crédits EdenAI.

À partir du solde de crédits d'un compte et de ses dépenses récentes (table Cost
sur les 7 derniers jours), estime combien de temps les crédits vont durer :
- dépense journalière moyenne (sur 7 jours) ;
- autonomie en jours (solde / dépense journalière) ;
- date de rupture estimée.

La projection est volontairement simple (linéaire) : elle donne un ordre de
grandeur, pas une prévision fine. Elle se dégrade proprement quand il n'y a pas
assez de données (aucune dépense récente → autonomie indéterminée).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func

from alambic_core.models import Cost

# Fenêtre d'observation des dépenses pour la moyenne journalière.
LOOKBACK_DAYS = 7


@dataclass
class CreditForecast:
    """Projection d'autonomie des crédits pour un compte."""

    credits: float | None = None          # solde actuel ($)
    spend_last_7d: float = 0.0            # dépense cumulée sur la fenêtre
    daily_spend: float = 0.0             # dépense journalière moyenne
    days_remaining: float | None = None  # autonomie estimée (jours)
    depletion_date: datetime | None = None  # date de rupture estimée
    has_data: bool = False               # assez de données pour projeter ?


def spend_last_days(session, account_id: str | None, days: int = LOOKBACK_DAYS,
                    *, now: datetime | None = None) -> float:
    """Somme des coûts d'un compte sur les `days` derniers jours."""
    since = (now or datetime.now(UTC)) - timedelta(days=days)
    q = session.query(func.coalesce(func.sum(Cost.amount), 0)).filter(Cost.created_at >= since)
    if account_id:
        q = q.filter(Cost.account_id == account_id)
    total = q.scalar()
    return float(total or 0)


def forecast_for_account(session, account_id: str | None, credits: float | None,
                         *, now: datetime | None = None) -> CreditForecast:
    """Construit la projection d'autonomie pour un compte.

    credits : solde EdenAI actuel (depuis edenai_credits). Si None (indisponible),
    on renvoie quand même la dépense observée, sans autonomie.
    """
    now = now or datetime.now(UTC)
    spend = spend_last_days(session, account_id, LOOKBACK_DAYS, now=now)
    daily = spend / LOOKBACK_DAYS if spend > 0 else 0.0

    forecast = CreditForecast(
        credits=credits,
        spend_last_7d=round(spend, 4),
        daily_spend=round(daily, 4),
        has_data=daily > 0,
    )

    # Autonomie : seulement si on a un solde ET une dépense journalière > 0.
    if credits is not None and daily > 0:
        days = credits / daily
        forecast.days_remaining = round(days, 1)
        forecast.depletion_date = now + timedelta(days=days)

    return forecast
