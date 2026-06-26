"""
Blueprint users — gestion des utilisateurs avec invitations.

Permissions :
  - SUPER_ADMIN : gère tous les utilisateurs de tous les comptes.
  - ADMIN       : gère uniquement les utilisateurs de son propre compte, et ne
                  peut pas créer de super-admin.
La création passe par une invitation : l'utilisateur reçoit un lien (affiché à
l'admin pour l'instant ; email plus tard) où il définit son mot de passe.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.domain.enums import UserRole
from alambic_core.models import Account, User
from alambic_core.security.invitations import issue_invitation
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..forms import UserForm
from .auth import admin_required

users_bp = Blueprint("users", __name__, url_prefix="/users")


def _session():
    return get_sessionmaker()()


def _visible_accounts(session):
    """Comptes que l'utilisateur courant peut affecter."""
    q = session.query(Account).order_by(Account.account_name)
    if current_user.is_super_admin:
        return q.all()
    # Admin : limité à son propre compte.
    return q.filter(Account.id == current_user.account_id).all()


def _can_manage(target: User) -> bool:
    """L'utilisateur courant peut-il gérer cet utilisateur cible ?"""
    if current_user.is_super_admin:
        return True
    # Admin : seulement les utilisateurs de son compte, et pas les super-admins.
    if target.role == UserRole.SUPER_ADMIN.value:
        return False
    return target.account_id == current_user.account_id


def _populate_account_choices(form, session):
    accounts = _visible_accounts(session)
    form.account_id.choices = [("", "— Aucun (transverse) —")] + [
        (a.id, a.account_name) for a in accounts
    ]


def _allowed_roles() -> set[str]:
    """Rôles que l'utilisateur courant a le droit d'attribuer."""
    if current_user.is_super_admin:
        return {UserRole.VALIDATOR.value, UserRole.ADMIN.value, UserRole.SUPER_ADMIN.value}
    # Un admin ne peut pas créer de super-admin.
    return {UserRole.VALIDATOR.value, UserRole.ADMIN.value}


@users_bp.route("/")
@admin_required
def list_users():
    with _session() as s:
        q = s.query(User).order_by(User.email)
        if not current_user.is_super_admin:
            q = q.filter(User.account_id == current_user.account_id)
        users = q.all()
        s.expunge_all()
    return render_template("users/list.html", users=users)


@users_bp.route("/new", methods=["GET", "POST"])
@admin_required
def create_user():
    with _session() as s:
        form = UserForm()
        _populate_account_choices(form, s)

        if form.validate_on_submit():
            if form.role.data not in _allowed_roles():
                flash("Vous ne pouvez pas attribuer ce rôle.", "error")
                return render_template("users/form.html", form=form, mode="create")

            # Email déjà pris ?
            existing = s.query(User).filter(User.email == form.email.data.strip().lower()).first()
            if existing is not None:
                flash("Un utilisateur avec cet email existe déjà.", "error")
                return render_template("users/form.html", form=form, mode="create")

            user = User(
                email=form.email.data,
                full_name=form.full_name.data or "",
                role=form.role.data,
                account_id=form.account_id.data or None,
                active=form.active.data,
                password_hash="",  # défini par l'utilisateur via l'invitation
            )
            s.add(user)
            s.flush()  # pour avoir l'id
            token = issue_invitation(s, user)
            invite_url = url_for("invite.accept_invite", token=token, _external=True)
            flash("Utilisateur créé. Transmettez-lui le lien d'invitation.", "success")
            # On affiche le lien une fois (email viendra plus tard).
            return render_template("users/invite_created.html", invite_url=invite_url, user=user)

        return render_template("users/form.html", form=form, mode="create")


@users_bp.route("/<user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id: str):
    with _session() as s:
        user = s.get(User, user_id)
        if user is None:
            flash("Utilisateur introuvable.", "error")
            return redirect(url_for("users.list_users"))
        if not _can_manage(user):
            abort(403)

        form = UserForm(obj=user) if request.method == "GET" else UserForm()
        _populate_account_choices(form, s)

        if form.validate_on_submit():
            if form.role.data not in _allowed_roles():
                flash("Vous ne pouvez pas attribuer ce rôle.", "error")
                return render_template("users/form.html", form=form, mode="edit", user_id=user_id)
            user.email = form.email.data
            user.full_name = form.full_name.data or ""
            user.role = form.role.data
            user.account_id = form.account_id.data or None
            user.active = form.active.data
            s.commit()
            flash("Utilisateur mis à jour.", "success")
            return redirect(url_for("users.list_users"))

        form.id.data = user.id
        form.role.data = user.role
        form.account_id.data = user.account_id or ""
        pending_invite = user.invite_token is not None
        s.expunge_all()
    return render_template(
        "users/form.html", form=form, mode="edit", user_id=user_id, pending_invite=pending_invite
    )


@users_bp.route("/<user_id>/reinvite", methods=["POST"])
@admin_required
def reinvite_user(user_id: str):
    with _session() as s:
        user = s.get(User, user_id)
        if user is None or not _can_manage(user):
            abort(403)
        token = issue_invitation(s, user)
        invite_url = url_for("invite.accept_invite", token=token, _external=True)
        return render_template("users/invite_created.html", invite_url=invite_url, user=user)


@users_bp.route("/<user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id: str):
    with _session() as s:
        user = s.get(User, user_id)
        if user is None or not _can_manage(user):
            abort(403)
        if user.id == current_user.id:
            flash("Vous ne pouvez pas vous supprimer vous-même.", "error")
            return redirect(url_for("users.list_users"))
        s.delete(user)
        s.commit()
        flash("Utilisateur supprimé.", "success")
    return redirect(url_for("users.list_users"))
