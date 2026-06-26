"""
Blueprint configs — CRUD des configurations de pipeline.

Une config relie un compte + un doctype à tout le paramétrage du traitement
(IA, entrées, sorties, export). Présentée en 6 onglets ; le mapping vers les
blocs JSONB et les secrets chiffrés est fait par config_schema.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Account, Config, Doctype
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..config_schema import (
    EXPORT_AUTH_TYPES,
    LLM_MODELS,
    LLM_PROVIDERS,
    OBJECT_DETECTION_PROVIDERS,
    OCR_LANGUAGES,
    OCR_PROVIDERS,
    WAY_IN_CHOICES,
    WAY_OUT_CHOICES,
    apply_form_to_config,
    default_values,
    parse_config,
    secret_presence,
)
from ..forms import ConfigForm
from .auth import admin_required

configs_bp = Blueprint("configs", __name__, url_prefix="/configs")


def _llm_catalog() -> dict:
    """Catalogue LLM (by_provider avec régions + liste des régions)."""
    from ..edenai_catalog import list_llms

    data = list_llms()
    return {
        "by_provider": data.get("by_provider", {}),
        "regions": data.get("regions", ["eu"]),
    }


def _ai_catalog() -> dict:
    """Catalogue AI pour les selects dynamiques de la page Config.

    Fournit, par feature, la structure {by_provider, regions} consommée par le
    front pour filtrer providers et modèles selon la région choisie :
      - classifier / extract : modèles LLM (mêmes données)
      - ocr : providers OCR
    (L'embedding ne figure plus ici : il est servi en local par TEI, hors config.)
    """
    from ..edenai_catalog import list_llms, list_providers

    llm = list_llms()
    ocr = list_providers("ocr", "ocr")

    llm_block = {
        "by_provider": llm.get("by_provider", {}),
        "regions": llm.get("regions", ["eu"]),
    }
    # OCR : list_providers renvoie une liste plate ; on l'adapte en by_provider.
    ocr_by_provider = {p: [] for p in ocr.get("providers", [])}
    return {
        "classifier": llm_block,
        "extract": llm_block,
        "ocr": {"by_provider": ocr_by_provider, "regions": ocr.get("regions", ["eu"])},
        "regions": sorted(set(llm.get("regions", ["eu"])) | {"eu", "global"}),
    }


# Listes de référence passées aux templates (datalists d'autocomplétion).
_REF_LISTS = {
    "way_in_choices": WAY_IN_CHOICES,
    "way_out_choices": WAY_OUT_CHOICES,
    "llm_providers": LLM_PROVIDERS,
    "ocr_providers": OCR_PROVIDERS,
    "llm_models": LLM_MODELS,
    "object_detection_providers": OBJECT_DETECTION_PROVIDERS,
    "ocr_languages": OCR_LANGUAGES,
}


def _session():
    return get_sessionmaker()()


def _visible_doctypes(session):
    """Doctypes affectables : publics + ceux du compte de l'admin."""
    from sqlalchemy import or_

    q = session.query(Doctype).order_by(Doctype.doctype_name)
    if not current_user.is_super_admin:
        q = q.filter(
            or_(Doctype.is_public.is_(True), Doctype.account_id == current_user.account_id)
        )
    return q.all()


def _populate_doctype_choices(form, session):
    doctypes = _visible_doctypes(session)
    form.doctype_id.choices = [("", "— Aucun —")] + [(d.id, d.doctype_name) for d in doctypes]


def _visible_accounts(session):
    q = session.query(Account).order_by(Account.account_name)
    if current_user.is_super_admin:
        return q.all()
    return q.filter(Account.id == current_user.account_id).all()


def _populate_account_choices(form, session):
    accounts = _visible_accounts(session)
    form.account_id.choices = [("", "— Aucun —")] + [(a.id, a.account_name) for a in accounts]


@configs_bp.route("/")
@admin_required
def list_configs():
    with _session() as s:
        q = s.query(Config).order_by(Config.config_name)
        if not current_user.is_super_admin:
            q = q.filter(Config.account_id == current_user.account_id)
        configs = q.all()
        s.expunge_all()
    return render_template("configs/list.html", configs=configs)


@configs_bp.route("/new", methods=["GET", "POST"])
@admin_required
def create_config():
    with _session() as s:
        form = ConfigForm()
        _populate_account_choices(form, s)
        _populate_doctype_choices(form, s)

        if form.validate_on_submit():
            cfg = Config(
                config_name=form.config_name.data,
                account_id=form.account_id.data or None,
            )
            apply_form_to_config(cfg, request.form)
            s.add(cfg)
            s.commit()
            flash("Configuration créée.", "success")
            return redirect(url_for("configs.list_configs"))

        return render_template(
            "configs/form.html",
            form=form,
            mode="create",
            values=default_values(),
            has_secret={},
            auth_types=EXPORT_AUTH_TYPES,
            llm_catalog=_llm_catalog(),
            ai_catalog=_ai_catalog(),
            **_REF_LISTS,
        )


@configs_bp.route("/<config_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_config(config_id: str):
    with _session() as s:
        cfg = s.get(Config, config_id)
        if cfg is None:
            flash("Configuration introuvable.", "error")
            return redirect(url_for("configs.list_configs"))

        form = ConfigForm(obj=cfg) if request.method == "GET" else ConfigForm()
        _populate_account_choices(form, s)
        _populate_doctype_choices(form, s)

        if form.validate_on_submit():
            cfg.config_name = form.config_name.data
            cfg.account_id = form.account_id.data or None
            apply_form_to_config(cfg, request.form)
            s.commit()
            flash("Configuration mise à jour.", "success")
            return redirect(url_for("configs.list_configs"))

        values = parse_config(cfg)
        has_secret = secret_presence(cfg)
        form.id.data = cfg.id
        form.account_id.data = cfg.account_id or ""
        form.doctype_id.data = cfg.doctype_id or ""
        form.need_validation.data = cfg.need_validation
        form.multi_doc_detect.data = cfg.multi_doc_detect
        s.expunge_all()
    return render_template(
        "configs/form.html",
        form=form,
        mode="edit",
        config_id=config_id,
        values=values,
        has_secret=has_secret,
        auth_types=EXPORT_AUTH_TYPES,
        llm_catalog=_llm_catalog(),
        ai_catalog=_ai_catalog(),
        **_REF_LISTS,
    )


@configs_bp.route("/edenai/providers/<feature>/<subtype>")
@admin_required
def edenai_providers(feature: str, subtype: str):
    """Proxy backend → catalogue EdenAI (anonyme, endpoint EU).

    Reprend le format FlowerScan : feature/subtype dans le chemin.
    """
    from flask import jsonify

    from ..edenai_catalog import list_providers

    return jsonify(list_providers(feature, subtype))


@configs_bp.route("/edenai/llms")
@admin_required
def edenai_llms():
    """Proxy backend → modèles LLM EdenAI (anonyme, endpoint EU)."""
    from flask import jsonify

    from ..edenai_catalog import list_llms

    return jsonify(list_llms())


@configs_bp.route("/account-edenai")
@admin_required
def account_edenai():
    """Indique (JSON) si le compte sélectionné a une clé EdenAI définie."""
    from flask import jsonify

    account_id = request.args.get("account_id") or None
    has_key = False
    if account_id:
        with _session() as s:
            acc = s.get(Account, account_id)
            if acc is not None:
                has_key = bool((acc.edenai_secret_key or "").strip())
    return jsonify({"has_account_key": has_key})


@configs_bp.route("/doctypes-for-account")
@admin_required
def doctypes_for_account():
    """Renvoie (JSON) les doctypes affectables pour un compte : publics + compte."""
    from flask import jsonify
    from sqlalchemy import or_

    account_id = request.args.get("account_id") or None
    with _session() as s:
        q = s.query(Doctype).order_by(Doctype.doctype_name)
        # Un admin reste limité à son périmètre.
        if not current_user.is_super_admin:
            q = q.filter(
                or_(Doctype.is_public.is_(True), Doctype.account_id == current_user.account_id)
            )
            items = q.all()
        elif account_id:
            q = q.filter(or_(Doctype.is_public.is_(True), Doctype.account_id == account_id))
            items = q.all()
        else:
            # Super-admin sans compte choisi : doctypes publics uniquement.
            items = q.filter(Doctype.is_public.is_(True)).all()
        result = [{"id": d.id, "name": d.doctype_name} for d in items]
    return jsonify({"doctypes": result})


@configs_bp.route("/<config_id>/delete", methods=["POST"])
@admin_required
def delete_config(config_id: str):
    with _session() as s:
        cfg = s.get(Config, config_id)
        if cfg is None:
            abort(404)
        s.delete(cfg)
        s.commit()
        flash("Configuration supprimée.", "success")
    return redirect(url_for("configs.list_configs"))
