"""Blueprint dashboard — tableau de bord de supervision.

Agrège des métriques réelles depuis alambic_core :
- volumes : transactions et documents par statut, sur une période ;
- coûts : table Cost (déjà alimentée par OCR et CLASSIFY), par provider/process/mois ;
- activité récente : dernières transactions et leur état ;
- temps de traitement : durées moyennes par étape (TransactionStep.duration_ms).

Comme partout dans l'UI, un super-admin voit tout ; un admin de compte ne voit
que les données de son compte.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Cost, Document, Transaction, TransactionStep
from flask import Blueprint, render_template
from flask_login import current_user
from sqlalchemy import func

from .auth import admin_required

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _session():
    return get_sessionmaker()()


def _scope_account(query, model):
    """Restreint au compte courant si l'utilisateur n'est pas super-admin."""
    if not current_user.is_super_admin:
        return query.filter(model.account_id == current_user.account_id)
    return query


def _volumes(s) -> dict:
    """Compte transactions et documents, total et par statut."""
    tx_q = _scope_account(s.query(Transaction.status, func.count()), Transaction).group_by(
        Transaction.status
    )
    tx_by_status = {status or "?": n for status, n in tx_q.all()}

    doc_q = _scope_account(
        s.query(Document.status, func.count()).join(
            Transaction, Document.transaction_id == Transaction.id
        ),
        Transaction,
    ).group_by(Document.status)
    doc_by_status = {status or "?": n for status, n in doc_q.all()}

    # Activité des 30 derniers jours.
    since = datetime.now(UTC) - timedelta(days=30)
    recent_tx = _scope_account(
        s.query(func.count()).select_from(Transaction).filter(Transaction.created_at >= since),
        Transaction,
    ).scalar()

    return {
        "tx_total": sum(tx_by_status.values()),
        "tx_by_status": tx_by_status,
        "doc_total": sum(doc_by_status.values()),
        "doc_by_status": doc_by_status,
        "tx_last_30d": recent_tx or 0,
    }


def _costs(s) -> dict:
    """Agrège la table Cost : total, par process, par provider, par mois."""
    base = _scope_account(s.query(Cost), Cost)

    total = base.with_entities(func.coalesce(func.sum(Cost.amount), 0)).scalar() or 0

    by_process = {
        process or "?": float(amount or 0)
        for process, amount in _scope_account(s.query(Cost.process, func.sum(Cost.amount)), Cost)
        .group_by(Cost.process)
        .all()
    }

    by_provider = {
        provider or "?": float(amount or 0)
        for provider, amount in _scope_account(s.query(Cost.provider, func.sum(Cost.amount)), Cost)
        .group_by(Cost.provider)
        .all()
    }

    by_month = [
        {"period": f"{year}-{month}", "amount": float(amount or 0)}
        for year, month, amount in _scope_account(
            s.query(Cost.year, Cost.month, func.sum(Cost.amount)), Cost
        )
        .group_by(Cost.year, Cost.month)
        .order_by(Cost.year.desc(), Cost.month.desc())
        .limit(12)
        .all()
    ]

    return {
        "total": float(total),
        "by_process": by_process,
        "by_provider": by_provider,
        "by_month": list(reversed(by_month)),
    }


def _processing_times(s) -> list:
    """Durée moyenne (et nombre) par type d'étape, depuis TransactionStep."""
    rows = (
        _scope_account(
            s.query(
                TransactionStep.process,
                func.avg(TransactionStep.duration_ms),
                func.max(TransactionStep.duration_ms),
                func.count(),
            ).join(Transaction, TransactionStep.transaction_id == Transaction.id),
            Transaction,
        )
        .filter(TransactionStep.duration_ms.isnot(None))
        .group_by(TransactionStep.process)
        .all()
    )
    result = [
        {
            "process": process or "?",
            "avg_ms": int(avg or 0),
            "max_ms": int(mx or 0),
            "count": n,
        }
        for process, avg, mx, n in rows
    ]
    return sorted(result, key=lambda r: r["avg_ms"], reverse=True)


def _recent_transactions(s, limit: int = 10) -> list:
    """Dernières transactions (toutes étapes confondues), triées par activité."""
    rows = (
        _scope_account(s.query(Transaction), Transaction)
        .order_by(Transaction.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": tx.id,
            "status": tx.status,
            "process": tx.process,
            "nb_docs": tx.nb_docs,
            "updated_at": tx.updated_at,
        }
        for tx in rows
    ]


@dashboard_bp.route("/")
@admin_required
def index():
    """Tableau de bord : volumes, coûts, temps de traitement, activité récente."""
    s = _session()
    try:
        context = {
            "volumes": _volumes(s),
            "costs": _costs(s),
            "processing_times": _processing_times(s),
            "recent": _recent_transactions(s),
        }
    finally:
        s.close()
    return render_template("dashboard/index.html", **context)
