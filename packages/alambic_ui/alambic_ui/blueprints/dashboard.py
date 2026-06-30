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


# Périodes proposées dans le sélecteur (clé → (libellé, jours)).
_PERIODS = {
    "week": ("7 derniers jours", 7),
    "month": ("30 derniers jours", 30),
    "year": ("12 derniers mois", 365),
}
_DEFAULT_PERIOD = "month"


def _resolve_period(period: str | None) -> tuple[str, datetime]:
    """Renvoie (clé de période validée, date de début de fenêtre)."""
    key = period if period in _PERIODS else _DEFAULT_PERIOD
    _, days = _PERIODS[key]
    since = datetime.now(UTC) - timedelta(days=days)
    return key, since


def _scope_period(query, model, since):
    """Restreint la requête à la fenêtre temporelle (sur created_at)."""
    return query.filter(model.created_at >= since)


def _volumes(s, since) -> dict:
    """Compte transactions et documents sur la période, total et par statut."""
    tx_q = (
        _scope_period(
            _scope_account(s.query(Transaction.status, func.count()), Transaction),
            Transaction,
            since,
        )
        .group_by(Transaction.status)
    )
    tx_by_status = {status or "?": n for status, n in tx_q.all()}

    doc_q = _scope_period(
        _scope_account(
            s.query(Document.status, func.count()).join(
                Transaction, Document.transaction_id == Transaction.id
            ),
            Transaction,
        ),
        Transaction,
        since,
    ).group_by(Document.status)
    doc_by_status = {status or "?": n for status, n in doc_q.all()}

    return {
        "tx_total": sum(tx_by_status.values()),
        "tx_by_status": tx_by_status,
        "doc_total": sum(doc_by_status.values()),
        "doc_by_status": doc_by_status,
    }


def _costs(s, since) -> dict:
    """Agrège la table Cost : total, par process, par provider, par mois."""
    base = _scope_period(_scope_account(s.query(Cost), Cost), Cost, since)

    total = base.with_entities(func.coalesce(func.sum(Cost.amount), 0)).scalar() or 0

    by_process = {
        process or "?": float(amount or 0)
        for process, amount in _scope_period(
            _scope_account(s.query(Cost.process, func.sum(Cost.amount)), Cost), Cost, since
        )
        .group_by(Cost.process)
        .all()
    }

    by_provider = {
        provider or "?": float(amount or 0)
        for provider, amount in _scope_period(
            _scope_account(s.query(Cost.provider, func.sum(Cost.amount)), Cost), Cost, since
        )
        .group_by(Cost.provider)
        .all()
    }

    by_month = [
        {"period": f"{year}-{month}", "amount": float(amount or 0)}
        for year, month, amount in _scope_period(
            _scope_account(s.query(Cost.year, Cost.month, func.sum(Cost.amount)), Cost),
            Cost,
            since,
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


def _cost_projection(s, since) -> dict:
    """Projection du coût mensuel selon le volume, à partir des coûts réels.

    Calcule le coût moyen par document à partir des coûts enregistrés sur la
    période et du nombre de documents distincts ayant consommé de l'IA, puis
    projette ce coût unitaire sur des volumes mensuels types. Donne aussi le
    détail du coût moyen par étape (OCR, classification, extraction).
    """
    base = _scope_period(_scope_account(s.query(Cost), Cost), Cost, since)
    total = float(base.with_entities(func.coalesce(func.sum(Cost.amount), 0)).scalar() or 0)

    # Documents distincts ayant au moins une ligne de coût (= traités par l'IA).
    doc_count = (
        _scope_period(
            _scope_account(s.query(func.count(func.distinct(Cost.document_id))), Cost), Cost, since
        ).scalar()
        or 0
    )

    cost_per_doc = (total / doc_count) if doc_count else 0.0

    # Coût moyen par document, ventilé par étape (process).
    per_process_avg = {}
    if doc_count:
        for process, amount in (
            _scope_period(
                _scope_account(s.query(Cost.process, func.sum(Cost.amount)), Cost), Cost, since
            )
            .group_by(Cost.process)
            .all()
        ):
            per_process_avg[process or "?"] = float(amount or 0) / doc_count

    # Part du coût de classification due au LLM (le poste qui décroît à mesure que
    # le vectoriel apprend). Sert à encadrer la projection : le coût actuel est un
    # PLAFOND (dominé par le bootstrap tout-LLM) ; si le vectoriel absorbait la
    # classification, le coût par document tendrait vers un PLANCHER.
    llm_classify_cost = float(
        _scope_period(
            _scope_account(s.query(func.coalesce(func.sum(Cost.amount), 0)), Cost),
            Cost,
            since,
        )
        .filter(Cost.process == "CLASSIFY", Cost.source.like("llm_%"))
        .scalar()
        or 0
    )
    cost_per_doc_floor = ((total - llm_classify_cost) / doc_count) if doc_count else 0.0
    llm_cost_share = round(100.0 * llm_classify_cost / total, 1) if total else 0.0

    # Projection sur des volumes mensuels types.
    volumes = [1000, 5000, 10000, 50000, 100000]
    projection = [
        {
            "volume": v,
            "monthly_cost": round(cost_per_doc * v, 2),
            "monthly_cost_floor": round(cost_per_doc_floor * v, 2),
        }
        for v in volumes
    ]

    return {
        "sample_docs": int(doc_count),
        "sample_total": round(total, 4),
        "cost_per_doc": round(cost_per_doc, 5),
        "cost_per_doc_floor": round(cost_per_doc_floor, 5),
        "llm_cost_share": llm_cost_share,
        "per_process_avg": {k: round(v, 5) for k, v in per_process_avg.items()},
        "projection": projection,
    }


def _processing_times(s, since) -> list:
    """Durée moyenne (et nombre) par type d'étape, depuis TransactionStep."""
    rows = (
        _scope_period(
            _scope_account(
                s.query(
                    TransactionStep.process,
                    func.avg(TransactionStep.duration_ms),
                    func.max(TransactionStep.duration_ms),
                    func.count(),
                ).join(Transaction, TransactionStep.transaction_id == Transaction.id),
                Transaction,
            ),
            Transaction,
            since,
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


def _recent_transactions(s, since, limit: int = 10) -> list:
    """Dernières transactions de la période, triées par activité."""
    rows = (
        _scope_period(_scope_account(s.query(Transaction), Transaction), Transaction, since)
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
    from flask import request

    period_key, since = _resolve_period(request.args.get("period"))
    s = _session()
    try:
        context = {
            "volumes": _volumes(s, since),
            "costs": _costs(s, since),
            "projection": _cost_projection(s, since),
            "classification": _classification_breakdown(s, since),
            "processing_times": _processing_times(s, since),
            "recent": _recent_transactions(s, since),
            "period": period_key,
            "period_label": _PERIODS[period_key][0],
            "periods": [(k, label) for k, (label, _d) in _PERIODS.items()],
        }
    finally:
        s.close()
    return render_template("dashboard/index.html", **context)


def _classification_breakdown(s, since) -> dict:
    """Répartition des classifications par nœud de la cascade (lexical/embedding/llm).

    Donne, par nœud, le nombre de documents classés et le coût total. Permet de
    visualiser la part GRATUITE (lexical + embedding, calcul local) face à la part
    PAYANTE (llm, appel EdenAI), et de la voir grossir à mesure que le modèle
    vectoriel s'entraîne.

    Source de vérité : Cost.source ("lexical_v…"/"embedding_v…"/"llm_v…"). Repli
    sur le champ `details` ("source=…") pour les coûts antérieurs à la colonne.
    """
    from alambic_core.services.cost_tracking import classification_method

    rows = _scope_period(
        _scope_account(
            s.query(
                Cost.source,
                Cost.details,
                func.count(),
                func.coalesce(func.sum(Cost.amount), 0),
            ),
            Cost,
        ),
        Cost,
        since,
    ).filter(Cost.process == "CLASSIFY")
    rows = rows.group_by(Cost.source, Cost.details).all()

    nodes = {
        "lexical": {"count": 0, "cost": 0.0},
        "embedding": {"count": 0, "cost": 0.0},
        "llm": {"count": 0, "cost": 0.0},
    }
    for source, details, count, amount in rows:
        method = classification_method(source or "")
        if not method and details:
            # Repli historique : extraire "source=xxx" du texte details.
            for part in str(details).split():
                if part.startswith("source="):
                    method = classification_method(part[len("source="):])
                    break
        if method in nodes:
            nodes[method]["count"] += int(count or 0)
            nodes[method]["cost"] += float(amount or 0)

    total = sum(n["count"] for n in nodes.values())
    free = nodes["lexical"]["count"] + nodes["embedding"]["count"]
    paid = nodes["llm"]["count"]

    def _pct(n: int) -> float:
        return round(100.0 * n / total, 1) if total else 0.0

    return {
        "nodes": {
            k: {
                "count": v["count"],
                "cost": round(v["cost"], 5),
                "pct": _pct(v["count"]),
            }
            for k, v in nodes.items()
        },
        "total": total,
        "free_count": free,
        "paid_count": paid,
        "free_pct": _pct(free),
        "paid_pct": _pct(paid),
    }

