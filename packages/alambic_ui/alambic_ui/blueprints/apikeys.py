"""Blueprint d'administration des clés API.

CRUD des clés qui authentifient les web services. La valeur en clair d'une clé
n'est affichée QU'UNE FOIS, à sa création (elle n'est jamais stockée : seul son
hash l'est). Ensuite la liste ne montre que le préfixe. Réservé aux admins.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Account, ApiKey
from alambic_core.services.api_keys import expiry_from_days, generate_key
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from .auth import admin_required

apikeys_bp = Blueprint("apikeys", __name__, url_prefix="/apikeys")


def _session():
    return get_sessionmaker()()


def _visible_keys(session):
    q = session.query(ApiKey)
    if not current_user.is_super_admin:
        q = q.filter(ApiKey.account_id == current_user.account_id)
    return q.order_by(ApiKey.apikey_name).all()


def _account_name(session, account_id):
    if not account_id:
        return "Tous"
    acc = session.get(Account, account_id)
    return acc.account_name if acc else account_id


@apikeys_bp.route("/")
@admin_required
def index():
    with _session() as s:
        keys = _visible_keys(s)
        rows = [
            {
                "id": k.id,
                "name": k.apikey_name,
                "prefix": k.key_prefix,
                "account": _account_name(s, k.account_id),
                "is_admin": k.is_admin,
                "is_active": k.is_active,
                "expires_at": k.expires_at,
            }
            for k in keys
        ]
    return render_template("apikeys/list.html", keys=rows)


@apikeys_bp.route("/create", methods=["POST"])
@admin_required
def create():
    name = (request.form.get("apikey_name") or "").strip()
    if not name:
        flash("Le nom de la clé ne peut pas être vide.", "error")
        return redirect(url_for("apikeys.index"))

    is_admin = request.form.get("is_admin") in ("on", "true", "1", "True")
    account_id = (request.form.get("account_id") or "").strip() or None
    # Une clé admin couvre tous les comptes : on ignore l'account_id.
    if is_admin:
        account_id = None
    # Périmètre : un admin non super ne crée que pour son compte.
    if not current_user.is_super_admin:
        account_id = current_user.account_id
        is_admin = False

    try:
        validity = int(request.form.get("validity") or 0)
    except ValueError:
        validity = 0

    gen = generate_key()
    with _session() as s:
        s.add(
            ApiKey(
                apikey_name=name,
                key_hash=gen.key_hash,
                key_prefix=gen.key_prefix,
                account_id=account_id,
                is_admin=is_admin,
                is_active=True,
                expires_at=expiry_from_days(validity),
            )
        )
        s.commit()

    # La valeur en clair n'est montrée QU'ICI, une seule fois.
    flash(
        f"Clé « {name} » créée. Copiez-la maintenant, elle ne sera plus jamais "
        f"affichée : {gen.plaintext}",
        "apikey_secret",
    )
    return redirect(url_for("apikeys.index"))


@apikeys_bp.route("/<key_id>/toggle", methods=["POST"])
@admin_required
def toggle(key_id: str):
    """Active/désactive une clé sans la supprimer."""
    with _session() as s:
        key = s.get(ApiKey, key_id)
        if key is None:
            flash("Clé introuvable.", "error")
            return redirect(url_for("apikeys.index"))
        if not current_user.is_super_admin and key.account_id != current_user.account_id:
            flash("Clé non autorisée.", "error")
            return redirect(url_for("apikeys.index"))
        key.is_active = not key.is_active
        s.commit()
        state = "activée" if key.is_active else "désactivée"
    flash(f"Clé {state}.", "success")
    return redirect(url_for("apikeys.index"))


@apikeys_bp.route("/<key_id>/delete", methods=["POST"])
@admin_required
def delete(key_id: str):
    with _session() as s:
        key = s.get(ApiKey, key_id)
        if key is None:
            flash("Clé introuvable.", "error")
            return redirect(url_for("apikeys.index"))
        if not current_user.is_super_admin and key.account_id != current_user.account_id:
            flash("Clé non autorisée.", "error")
            return redirect(url_for("apikeys.index"))
        s.delete(key)
        s.commit()
    flash("Clé supprimée.", "success")
    return redirect(url_for("apikeys.index"))
