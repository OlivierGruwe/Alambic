"""
Blueprint accounts — CRUD des comptes clients sur alambic_core.

Remplace le CRUD générique de FlowerScan (flowerscan_lib) par des appels directs
à AccountRepository. Réservé aux administrateurs.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Account
from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..config_schema import _is_placeholder_secret
from ..forms import AccountForm
from .auth import admin_required

accounts_bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def _session():
    return get_sessionmaker()()


@accounts_bp.route("/")
@admin_required
def list_accounts():
    with _session() as s:
        accounts = s.query(Account).order_by(Account.account_name).all()
        # Détacher pour usage hors session (template).
        s.expunge_all()
    return render_template("accounts/list.html", accounts=accounts)


def _address_to_block(form) -> dict:
    """Regroupe les 5 lignes d'adresse du formulaire en bloc JSONB (line1..line5)."""
    block = {}
    for i in range(1, 6):
        val = (getattr(form, f"address{i}").data or "").strip()
        if val:
            block[f"line{i}"] = val
    return block


def _block_to_form(form, account) -> None:
    """Pré-remplit les 5 lignes d'adresse du formulaire depuis le bloc JSONB."""
    block = account.address or {}
    for i in range(1, 6):
        getattr(form, f"address{i}").data = block.get(f"line{i}", "")


def _apply_common_fields(acc, form) -> None:
    """Applique les champs communs (hors secret) du formulaire au modèle."""
    from alambic_core.domain.naming import to_snake_case

    acc.account_name = to_snake_case(form.account_name.data)
    acc.active = form.active.data
    acc.address = _address_to_block(form)
    acc.zip = form.zip.data or ""
    acc.town = form.town.data or ""
    acc.country = form.country.data or ""
    acc.enrich_allowed_domains = form.enrich_allowed_domains.data or ""


@accounts_bp.route("/new", methods=["GET", "POST"])
@admin_required
def create_account():
    form = AccountForm()
    if form.validate_on_submit():
        with _session() as s:
            acc = Account()
            _apply_common_fields(acc, form)
            # Secret : à la création, on pose la valeur saisie (si présente).
            key_val = form.edenai_secret_key.data
            if key_val and not _is_placeholder_secret(key_val):
                acc.edenai_secret_key = key_val
            s.add(acc)
            s.commit()
        flash("Compte créé.", "success")
        return redirect(url_for("accounts.list_accounts"))
    return render_template("accounts/form.html", form=form, mode="create")


@accounts_bp.route("/<account_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_account(account_id: str):
    with _session() as s:
        acc = s.get(Account, account_id)
        if acc is None:
            flash("Compte introuvable.", "error")
            return redirect(url_for("accounts.list_accounts"))

        if request.method == "GET":
            form = AccountForm(obj=acc)
            _block_to_form(form, acc)
            form.id.data = acc.id
            # On ne pré-remplit JAMAIS le secret (champ masqué).
            form.edenai_secret_key.data = ""
            has_secret = bool(acc.edenai_secret_key)
            s.expunge_all()
            return render_template(
                "accounts/form.html",
                form=form,
                mode="edit",
                account_id=account_id,
                has_secret=has_secret,
            )

        form = AccountForm()
        if form.validate_on_submit():
            _apply_common_fields(acc, form)
            # Secret masqué : vide = on conserve l'existant ; saisi = on remplace.
            key_val = form.edenai_secret_key.data
            if key_val and not _is_placeholder_secret(key_val):
                acc.edenai_secret_key = key_val
            s.commit()
            flash("Compte mis à jour.", "success")
            return redirect(url_for("accounts.list_accounts"))

        form.id.data = acc.id
        has_secret = bool(acc.edenai_secret_key)
        s.expunge_all()
    return render_template(
        "accounts/form.html",
        form=form,
        mode="edit",
        account_id=account_id,
        has_secret=has_secret,
    )


@accounts_bp.route("/<account_id>/toggle", methods=["POST"])
@admin_required
def toggle_account(account_id: str):
    with _session() as s:
        acc = s.get(Account, account_id)
        if acc is not None:
            acc.active = not acc.active
            s.commit()
            flash(f"Compte {'activé' if acc.active else 'désactivé'}.", "success")
    return redirect(url_for("accounts.list_accounts"))
