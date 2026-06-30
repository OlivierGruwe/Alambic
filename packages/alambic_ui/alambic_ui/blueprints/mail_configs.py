"""Blueprint de gestion des configurations mail (IMAP).

CRUD des boîtes mail relevées périodiquement par le worker. Le mot de passe IMAP
est préservé s'il n'est pas re-saisi (vide = inchangé), comme les autres secrets.
Réservé aux admins.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.domain.naming import to_snake_case
from alambic_core.models import Account, Config, MailConfig
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..config_schema import _is_placeholder_secret
from ..forms import MailConfigForm
from .auth import admin_required

mail_configs_bp = Blueprint("mail_configs", __name__, url_prefix="/mail-configs")


def _session():
    return get_sessionmaker()()


def _visible_accounts(session):
    q = session.query(Account)
    if not current_user.is_super_admin:
        q = q.filter(Account.id == current_user.account_id)
    return q.order_by(Account.account_name).all()


def _populate_choices(form, session):
    accounts = _visible_accounts(session)
    form.account_id.choices = [("", "— Aucun —")] + [
        (a.id, a.account_name) for a in accounts
    ]
    configs = session.query(Config).order_by(Config.config_name).all()
    form.config_id.choices = [("", "— Aucune —")] + [
        (c.id, c.config_name) for c in configs
    ]


def _apply_form(mc, form, *, is_new: bool) -> None:
    """Applique le formulaire au modèle. Mot de passe préservé si non saisi."""
    mc.mailconfig_name = to_snake_case(form.mailconfig_name.data)
    mc.email_address = (form.email_address.data or "").strip()
    mc.config_id = form.config_id.data or None
    mc.account_id = form.account_id.data or None
    mc.is_active = form.is_active.data
    mc.imap_server = (form.imap_server.data or "").strip()
    mc.imap_port = form.imap_port.data or 993
    mc.imap_inbox = (form.imap_inbox.data or "INBOX").strip()
    mc.imap_search_criteria = (form.imap_search_criteria.data or "(UNSEEN)").strip()
    mc.imap_alias = (form.imap_alias.data or "").strip()
    mc.content_mode = form.content_mode.data or "all"
    mc.filter_attachment_extensions = (form.filter_attachment_extensions.data or "").strip()
    mc.sender_whitelist = (form.sender_whitelist.data or "").strip()
    mc.after_process_action = form.after_process_action.data or "seen"
    mc.after_process_folder = (form.after_process_folder.data or "ARCHIVE").strip()

    # Secret : on ne remplace QUE si une valeur réelle est saisie (vide = inchangé,
    # et une valeur composée de puces « •••• » d'autofill est ignorée).
    password = form.imap_password.data or ""
    if password and not _is_placeholder_secret(password):
        mc.imap_password_enc = password


@mail_configs_bp.route("/")
@admin_required
def list_mail_configs():
    with _session() as s:
        q = s.query(MailConfig)
        if not current_user.is_super_admin:
            q = q.filter(MailConfig.account_id == current_user.account_id)
        configs = q.order_by(MailConfig.mailconfig_name).all()
        return render_template("mail_configs/list.html", mail_configs=configs)


@mail_configs_bp.route("/new", methods=["GET", "POST"])
@admin_required
def create_mail_config():
    with _session() as s:
        form = MailConfigForm()
        _populate_choices(form, s)
        if form.validate_on_submit():
            mc = MailConfig()
            _apply_form(mc, form, is_new=True)
            s.add(mc)
            s.commit()
            flash("Configuration mail créée.", "success")
            return redirect(url_for("mail_configs.list_mail_configs"))
        return render_template("mail_configs/form.html", form=form, mode="create")


@mail_configs_bp.route("/<mail_config_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_mail_config(mail_config_id: str):
    with _session() as s:
        mc = s.get(MailConfig, mail_config_id)
        if mc is None:
            flash("Configuration mail introuvable.", "error")
            return redirect(url_for("mail_configs.list_mail_configs"))

        form = MailConfigForm(obj=mc) if request.method == "GET" else MailConfigForm()
        _populate_choices(form, s)
        # Ne jamais pré-remplir le mot de passe dans le formulaire.
        if request.method == "GET":
            form.imap_password.data = ""

        if form.validate_on_submit():
            _apply_form(mc, form, is_new=False)
            s.commit()
            flash("Configuration mail mise à jour.", "success")
            return redirect(url_for("mail_configs.list_mail_configs"))

        has_secret = bool(mc.imap_password_enc)
        return render_template(
            "mail_configs/form.html", form=form, mode="edit", has_secret=has_secret
        )


@mail_configs_bp.route("/<mail_config_id>/delete", methods=["POST"])
@admin_required
def delete_mail_config(mail_config_id: str):
    with _session() as s:
        mc = s.get(MailConfig, mail_config_id)
        if mc is not None:
            s.delete(mc)
            s.commit()
            flash("Configuration mail supprimée.", "success")
    return redirect(url_for("mail_configs.list_mail_configs"))
