"""
Blueprint transactions — déclenchement de traitements par dépôt de documents.

L'upload ne crée pas la transaction directement : il dépose le fichier dans
Garage sous la clé __uploads__/<account>/<config>/<origin>/<filename>. Le worker
(alambic_workers.tasks.start_ingestion) détecte le dépôt, crée la transaction et
lance le pipeline. L'UI ne connaît donc ni Celery ni la base des transactions
pour cette étape — elle ne fait que déposer au bon endroit.
"""

from __future__ import annotations

from datetime import UTC

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Config
from alambic_core.storage import build_upload_key, input_bucket, put_bytes
from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.utils import secure_filename

from .auth import admin_required

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")


def _session():
    return get_sessionmaker()()


def _visible_configs(session):
    """Configs utilisables : toutes pour un super-admin, sinon celles du compte."""
    q = session.query(Config).order_by(Config.config_name)
    if not current_user.is_super_admin:
        q = q.filter(Config.account_id == current_user.account_id)
    return q.all()


_SORTABLE_COLUMNS = {
    "creation_date": "created_at",
    "account_id": "account_id",
    "config__id": "config_id",
    "status": "status",
    "process": "process",
    "author": "author",
}

_PAGE_SIZE = 25


def _visible_transactions(session, *, sort_by="creation_date", order="desc", page=1):
    """Transactions visibles (filtrées par compte), triées et paginées.

    Renvoie (liste, has_next). Le tri porte sur une colonne autorisée
    (_SORTABLE_COLUMNS) ; tout autre valeur retombe sur la date. La pagination
    récupère une page de _PAGE_SIZE + 1 lignes pour savoir s'il y a une suite.
    """
    from alambic_core.models import Transaction

    column_name = _SORTABLE_COLUMNS.get(sort_by, "created_at")
    column = getattr(Transaction, column_name)
    direction = column.asc() if order == "asc" else column.desc()

    q = session.query(Transaction).order_by(direction)
    if not current_user.is_super_admin:
        q = q.filter(Transaction.account_id == current_user.account_id)

    page = max(1, page)
    rows = q.offset((page - 1) * _PAGE_SIZE).limit(_PAGE_SIZE + 1).all()
    has_next = len(rows) > _PAGE_SIZE
    return rows[:_PAGE_SIZE], has_next


@transactions_bp.route("/")
@admin_required
def index():
    """Page transactions : liste des traitements + dépôt pour en déclencher un."""
    from datetime import datetime, timedelta

    from alambic_core.models import Account, Config, Cost, Document
    from alambic_core.services.transaction_status import (
        compute_transaction_status,
        count_active_documents,
    )
    from alambic_core.services.validation import validation_summary
    from sqlalchemy import func

    sort_by = request.args.get("sort_by", "creation_date")
    order = request.args.get("order", "desc")
    if order not in ("asc", "desc"):
        order = "desc"
    try:
        page = int(request.args.get("page", 1))
    except (TypeError, ValueError):
        page = 1

    stuck_threshold = timedelta(minutes=10)
    now = datetime.now(UTC)

    with _session() as s:
        configs = _visible_configs(s)
        items = [{"id": c.id, "name": c.config_name, "account_id": c.account_id} for c in configs]

        txs, has_next = _visible_transactions(s, sort_by=sort_by, order=order, page=page)
        account_names = {a.id: a.account_name for a in s.query(Account).all()}
        config_names = {c.id: c.config_name for c in s.query(Config).all()}

        # Coût par transaction (somme de la table Cost).
        cost_rows = (
            s.query(Cost.transaction_id, func.sum(Cost.amount)).group_by(Cost.transaction_id).all()
        )
        costs_by_tx = {tx_id: float(amount or 0) for tx_id, amount in cost_rows}

        transactions = []
        for tx in txs:
            # Documents (objets) pour recalcul + comptage + résumé de validation.
            docs = s.query(Document).filter(Document.transaction_id == tx.id).all()
            doc_statuses = [{"status": d.status} for d in docs]
            status = compute_transaction_status(tx.status, doc_statuses)

            # "Bloquée" : WORKING depuis plus de 10 minutes (→ bouton relancer).
            updated = tx.updated_at
            if updated is not None and updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            is_stuck = (
                status == "WORKING" and updated is not None and (now - updated) > stuck_threshold
            )

            transactions.append(
                {
                    "id": tx.id,
                    "created_at": tx.created_at,
                    "account": account_names.get(tx.account_id, "—"),
                    "config": config_names.get(tx.config_id, "—"),
                    "status": status,
                    "process": tx.process,
                    "nb_docs": count_active_documents(doc_statuses),
                    "cost": costs_by_tx.get(tx.id),
                    "is_working": status == "WORKING",
                    "is_stuck": is_stuck,
                    "validation": validation_summary(docs),
                }
            )
    return render_template(
        "transactions/index.html",
        configs=items,
        transactions=transactions,
        sort_by=sort_by,
        order=order,
        page=page,
        has_next=has_next,
    )


@transactions_bp.route("/statuses")
@admin_required
def statuses():
    """Statuts courants des transactions (polling léger, JSON).

    Renvoie pour chaque transaction visible son statut recalculé et son nombre
    de documents actifs. Le client ne rafraîchit que les lignes en cours.
    """
    from alambic_core.models import Document
    from alambic_core.services.transaction_status import (
        compute_transaction_status,
        count_active_documents,
    )
    from flask import jsonify

    with _session() as s:
        txs, _ = _visible_transactions(s)
        out = {}
        for tx in txs:
            doc_statuses = [
                {"status": st}
                for (st,) in s.query(Document.status).filter(Document.transaction_id == tx.id).all()
            ]
            out[tx.id] = {
                "status": compute_transaction_status(tx.status, doc_statuses),
                "nb_docs": count_active_documents(doc_statuses),
            }
    return jsonify(out)


@transactions_bp.route("/<tx_id>/retry", methods=["POST"])
@admin_required
def retry(tx_id: str):
    """Relance une transaction bloquée (réinjecte ses documents non terminés)."""
    from alambic_core.models import Document, Transaction
    from alambic_core.services.transaction_status import compute_transaction_status

    with _session() as s:
        tx = s.get(Transaction, tx_id)
        if tx is None:
            flash("Transaction introuvable.", "error")
            return redirect(url_for("transactions.index"))
        if not current_user.is_super_admin and tx.account_id != current_user.account_id:
            flash("Accès refusé.", "error")
            return redirect(url_for("transactions.index"))
        # On ne relance qu'une transaction effectivement en cours (WORKING).
        doc_statuses = [
            {"status": st}
            for (st,) in s.query(Document.status).filter(Document.transaction_id == tx_id).all()
        ]
        status = compute_transaction_status(tx.status, doc_statuses)

    if status != "WORKING":
        flash("Seule une transaction en cours peut être relancée.", "error")
        return redirect(url_for("transactions.index"))

    try:
        from alambic_workers.tasks.retry import retry_transaction

        result = retry_transaction(tx_id)
        flash(f"Transaction relancée ({result['relaunched']} document(s)).", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"Échec de la relance : {exc}", "error")
    return redirect(url_for("transactions.index"))


@transactions_bp.route("/<tx_id>/delete", methods=["POST"])
@admin_required
def delete(tx_id: str):
    """Supprime une transaction terminée ou en erreur (pas en cours)."""
    from alambic_core.models import Document, Transaction
    from alambic_core.services.deletion import delete_transaction
    from alambic_core.services.transaction_status import compute_transaction_status

    with _session() as s:
        tx = s.get(Transaction, tx_id)
        if tx is None:
            flash("Transaction introuvable.", "error")
            return redirect(url_for("transactions.index"))
        if not current_user.is_super_admin and tx.account_id != current_user.account_id:
            flash("Accès refusé.", "error")
            return redirect(url_for("transactions.index"))
        doc_statuses = [
            {"status": st}
            for (st,) in s.query(Document.status).filter(Document.transaction_id == tx_id).all()
        ]
        status = compute_transaction_status(tx.status, doc_statuses)

    # Garde : pas de suppression d'une transaction en cours.
    if status == "WORKING":
        flash("Impossible de supprimer une transaction en cours.", "error")
        return redirect(url_for("transactions.index"))

    try:
        delete_transaction(tx_id)
        flash("Transaction supprimée.", "success")
    except Exception as exc:  # noqa: BLE001
        flash(f"Échec de la suppression : {exc}", "error")
    return redirect(url_for("transactions.index"))


@transactions_bp.route("/<tx_id>/documents")
@admin_required
def documents(tx_id: str):
    """Documents d'une transaction (chargement lazy au dépliage).

    Renvoie un fragment HTML des documents 'finaux' (on exclut les parents
    dépréciés par le découpage). Vérifie l'appartenance au compte.
    """
    from alambic_core.domain.enums import DocumentStatus
    from alambic_core.models import Document, Transaction

    with _session() as s:
        tx = s.get(Transaction, tx_id)
        if tx is None:
            return render_template(
                "transactions/_documents.html", documents=[], error="Transaction introuvable."
            )
        if not current_user.is_super_admin and tx.account_id != current_user.account_id:
            return render_template(
                "transactions/_documents.html", documents=[], error="Accès refusé."
            )

        rows = (
            s.query(Document)
            .filter(Document.transaction_id == tx_id)
            .filter(
                Document.status.notin_(
                    [DocumentStatus.DEPRECATED.value, DocumentStatus.DISCARDED.value]
                )
            )
            .order_by(Document.id)
            .all()
        )
        docs = [{"id": d.id, "doctype": d.doctype, "status": d.status} for d in rows]
    return render_template("transactions/_documents.html", documents=docs, error=None)


@transactions_bp.route("/upload", methods=["POST"])
@admin_required
def upload():
    """Dépose les fichiers dans Garage sous la clé d'ingestion.

    Le worker prend le relais (détection du dépôt → création de transaction).
    """
    config_id = (request.form.get("config_id") or "").strip()
    if not config_id:
        flash("Veuillez choisir une configuration.", "error")
        return redirect(url_for("transactions.index"))

    with _session() as s:
        conf = s.get(Config, config_id)
        if conf is None:
            flash("Configuration introuvable.", "error")
            return redirect(url_for("transactions.index"))
        # Vérif de périmètre : un admin ne dépose que pour son compte.
        if not current_user.is_super_admin and conf.account_id != current_user.account_id:
            flash("Configuration non autorisée.", "error")
            return redirect(url_for("transactions.index"))
        account_id = conf.account_id or ""

    files = request.files.getlist("files")
    files = [f for f in files if f and f.filename]
    if not files:
        flash("Aucun fichier sélectionné.", "error")
        return redirect(url_for("transactions.index"))

    bucket = input_bucket()
    deposited, errors = [], []
    for file in files:
        filename = secure_filename(file.filename)
        try:
            content = file.read()
            if not content:
                errors.append(f"{filename} : fichier vide")
                continue
            key = build_upload_key(account_id, config_id, filename)
            put_bytes(
                bucket,
                key,
                content,
                metadata={
                    "author": getattr(current_user, "email", ""),
                    "origin": "import",
                },
            )
            deposited.append(filename)
        except Exception as exc:  # storage indisponible, etc.
            errors.append(f"{filename} : {exc}")

    if deposited:
        flash(
            f"{len(deposited)} document(s) déposé(s). Le traitement va démarrer automatiquement.",
            "success",
        )
    for err in errors:
        flash(err, "error")
    return redirect(url_for("transactions.index"))


# ── Suppression groupée ─────────────────────────────────────────────────────


@transactions_bp.route("/delete-bulk", methods=["POST"])
@admin_required
def delete_bulk():
    """Supprime plusieurs transactions sélectionnées (cases à cocher du tableau)."""
    from alambic_core.services import deletion

    ids = request.form.getlist("transaction_ids")
    if not ids:
        flash("Aucune transaction sélectionnée.", "error")
        return redirect(url_for("transactions.index"))

    deleted, errors = 0, 0
    with _session() as s:
        from alambic_core.models import Transaction

        for tx_id in ids:
            tx = s.get(Transaction, tx_id)
            if tx is None:
                continue
            # Garde d'isolation : un non-super-admin ne supprime que son compte.
            if not current_user.is_super_admin and tx.account_id != current_user.account_id:
                continue
            try:
                deletion.delete_transaction(tx_id)
                deleted += 1
            except Exception:  # noqa: BLE001
                errors += 1

    if deleted:
        flash(f"{deleted} transaction(s) supprimée(s).", "success")
    if errors:
        flash(f"{errors} suppression(s) en échec.", "error")
    return redirect(url_for("transactions.index"))


# ── Validation humaine des index ────────────────────────────────────────────


@transactions_bp.route("/documents/<doc_id>/pdf")
@admin_required
def document_pdf(doc_id: str):
    """Sert le PDF d'un document depuis Garage (pour le visualiseur de validation)."""
    from alambic_core import storage
    from alambic_core.models import Document
    from flask import Response

    with _session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            abort(404)
        if not current_user.is_super_admin:
            tx = doc.transaction
            if tx is not None and tx.account_id != current_user.account_id:
                abort(403)
        bucket = doc.bucket_name or storage.work_bucket()
        key = doc.object_key

    if not key:
        abort(404)
    try:
        content = storage.get_bytes(bucket, key)
    except Exception:  # noqa: BLE001
        abort(404)
    return Response(content, mimetype="application/pdf")


@transactions_bp.route("/documents/<doc_id>/indexes")
@admin_required
def document_indexes(doc_id: str):
    """Renvoie les index extraits d'un document (JSON) pour l'écran de validation."""
    from alambic_core.models import Document
    from alambic_core.services.validation import load_indexes

    with _session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            abort(404)
        if not current_user.is_super_admin:
            tx = doc.transaction
            if tx is not None and tx.account_id != current_user.account_id:
                abort(403)
        indexes = load_indexes(s, doc_id)
        payload = {
            "document_id": doc_id,
            "doctype": doc.doctype,
            "doctype_desc": doc.doctype_desc,
            "status": doc.status,
            "extraction_summary": doc.extraction_summary or {},
            "indexes": indexes,
        }
    return jsonify(payload)


@transactions_bp.route("/documents/<doc_id>/validate", methods=["POST"])
@admin_required
def document_validate(doc_id: str):
    """Enregistre les corrections d'index et valide le document (→ VALIDATED)."""
    from alambic_core.models import Document
    from alambic_core.services.validation import validate_document

    data = request.get_json(silent=True) or {}
    indexes = data.get("indexes")

    with _session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            abort(404)
        if not current_user.is_super_admin:
            tx = doc.transaction
            if tx is not None and tx.account_id != current_user.account_id:
                abort(403)
        ok = validate_document(s, doc_id, indexes=indexes)
        if not ok:
            abort(404)
        s.commit()
    return jsonify({"document_id": doc_id, "status": "VALIDATED"})


@transactions_bp.route("/documents/<doc_id>/save", methods=["POST"])
@admin_required
def document_save(doc_id: str):
    """Enregistre les corrections d'index sans valider (bouton Enregistrer)."""
    from alambic_core.models import Document
    from alambic_core.services.validation import save_indexes

    data = request.get_json(silent=True) or {}
    indexes = data.get("indexes") or []

    with _session() as s:
        doc = s.get(Document, doc_id)
        if doc is None:
            abort(404)
        if not current_user.is_super_admin:
            tx = doc.transaction
            if tx is not None and tx.account_id != current_user.account_id:
                abort(403)
        written = save_indexes(s, doc_id, indexes)
        s.commit()
    return jsonify({"document_id": doc_id, "saved": written})
