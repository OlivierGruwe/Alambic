"""Blueprint d'authentification : login / logout."""

from __future__ import annotations

from functools import wraps

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from ..forms import LoginForm

auth_bp = Blueprint("auth", __name__)


def admin_required(view):
    """Refuse l'accès aux non-admins (réservé super-admin + admin)."""

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_admin:
            flash("Accès réservé aux administrateurs.", "error")
            return redirect(url_for("auth.no_access"))
        return view(*args, **kwargs)

    return wrapped


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    # Déjà connecté → on évite de réafficher le login.
    if current_user.is_authenticated:
        return redirect(url_for("accounts.list_accounts"))

    form = LoginForm()
    if form.validate_on_submit():
        from .. import FlaskUser, get_auth_provider

        user = get_auth_provider().authenticate(form.email.data, form.password.data)
        if user is None:
            flash("Email ou mot de passe incorrect.", "error")
        else:
            login_user(FlaskUser(user))
            # Redirection vers la page demandée avant login, si présente.
            next_url = request.args.get("next")
            return redirect(next_url or url_for("accounts.list_accounts"))

    return render_template("login.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/no-access")
@login_required
def no_access():
    """Page neutre accessible à tout connecté (évite les boucles de redirection
    quand un utilisateur sans droit suffisant est refusé)."""
    return render_template("no_access.html")
