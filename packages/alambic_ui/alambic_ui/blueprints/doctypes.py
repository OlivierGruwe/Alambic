"""
Blueprint doctypes — CRUD des types de documents avec éditeur de champs.

Un doctype définit les champs à extraire d'un document. Le formulaire édite les
métadonnées (nom, public, compte) et la liste des champs ; la liste est
sérialisée en json_content via doctype_schema.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.models import Account, Config, Doctype
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..doctype_schema import (
    FIELD_SPEC,
    build_json_content,
    empty_field,
    parse_doctype,
)
from ..forms import DoctypeForm
from .auth import admin_required

doctypes_bp = Blueprint("doctypes", __name__, url_prefix="/doctypes")


def _session():
    return get_sessionmaker()()


def _visible_accounts(session):
    q = session.query(Account).order_by(Account.account_name)
    if current_user.is_super_admin:
        return q.all()
    return q.filter(Account.id == current_user.account_id).all()


def _populate_account_choices(form, session):
    accounts = _visible_accounts(session)
    form.account_id.choices = [("", "— Aucun (doctype global) —")] + [
        (a.id, a.account_name) for a in accounts
    ]


def _fields_from_post(form_data) -> list[dict]:
    """Reconstruit la liste des champs depuis les données POST dynamiques.

    Les champs sont indexés : fields-0-field_name, fields-0-regexp, etc.
    On détecte les index présents, et pour chaque on lit chaque attribut connu.
    Les cases à cocher absentes valent False (non cochées).
    """
    from ..doctype_schema import BOOL_KEYS

    keys = [k for k, _, _, _, _ in FIELD_SPEC]
    # Repérer les index présents (à partir de field_name notamment).
    indices = set()
    prefix = "fields-"
    for name in form_data:
        if name.startswith(prefix):
            rest = name[len(prefix) :]
            idx, _, attr = rest.partition("-")
            if idx.isdigit() and attr in keys:
                indices.add(int(idx))

    from alambic_core.domain.naming import to_snake_case

    fields = []
    for i in sorted(indices):
        field = {}
        for key in keys:
            field_name = f"{prefix}{i}-{key}"
            if key in BOOL_KEYS:
                field[key] = field_name in form_data  # présent = coché
            else:
                field[key] = form_data.get(field_name, "")
        # Le nom de champ est une clé technique : normalisé en snake_case.
        field["field_name"] = to_snake_case(field.get("field_name", ""))
        # Ignorer un champ entièrement vide (ligne ajoutée puis non remplie).
        if field.get("field_name", "").strip():
            fields.append(field)
    return fields


@doctypes_bp.route("/")
@admin_required
def list_doctypes():
    with _session() as s:
        q = s.query(Doctype).order_by(Doctype.doctype_name)
        if not current_user.is_super_admin:
            # Admin : doctypes publics + ceux de son compte.
            from sqlalchemy import or_

            q = q.filter(
                or_(Doctype.is_public.is_(True), Doctype.account_id == current_user.account_id)
            )
        doctypes = q.all()
        s.expunge_all()
    return render_template("doctypes/list.html", doctypes=doctypes)


@doctypes_bp.route("/new", methods=["GET", "POST"])
@admin_required
def create_doctype():
    with _session() as s:
        form = DoctypeForm()
        _populate_account_choices(form, s)

        if form.validate_on_submit():
            from alambic_core.domain.naming import to_snake_case

            dt_name = to_snake_case(form.doctype_name.data)
            fields = _fields_from_post(request.form)
            json_content = build_json_content(dt_name, fields)
            dt = Doctype(
                doctype_name=dt_name,
                is_public=form.is_public.data,
                account_id=form.account_id.data or None,
                json_content=json_content,
            )
            s.add(dt)
            s.commit()
            flash("Doctype créé.", "success")
            return redirect(url_for("doctypes.list_doctypes"))

        return render_template(
            "doctypes/form.html",
            form=form,
            mode="create",
            fields=[],
            field_spec=FIELD_SPEC,
            empty_field_dict=empty_field(),
        )


@doctypes_bp.route("/<doctype_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_doctype(doctype_id: str):
    with _session() as s:
        dt = s.get(Doctype, doctype_id)
        if dt is None:
            flash("Doctype introuvable.", "error")
            return redirect(url_for("doctypes.list_doctypes"))

        form = DoctypeForm(obj=dt) if request.method == "GET" else DoctypeForm()
        _populate_account_choices(form, s)

        if form.validate_on_submit():
            from alambic_core.domain.naming import to_snake_case

            dt_name = to_snake_case(form.doctype_name.data)
            fields = _fields_from_post(request.form)
            dt.doctype_name = dt_name
            dt.is_public = form.is_public.data
            dt.account_id = form.account_id.data or None
            dt.json_content = build_json_content(dt_name, fields)
            s.commit()
            flash("Doctype mis à jour.", "success")
            return redirect(url_for("doctypes.list_doctypes"))

        # GET : parser le json_content existant pour pré-remplir l'éditeur.
        parsed = parse_doctype(dt.json_content)
        form.id.data = dt.id
        form.account_id.data = dt.account_id or ""
        s.expunge_all()
    return render_template(
        "doctypes/form.html",
        form=form,
        mode="edit",
        doctype_id=doctype_id,
        fields=parsed["fields"],
        field_spec=FIELD_SPEC,
        empty_field_dict=empty_field(),
    )


@doctypes_bp.route("/generate-fields", methods=["POST"])
@admin_required
def generate_fields():
    """Génère des champs depuis un PDF uploadé (via EdenAI → LLM d'extraction).

    Les paramètres EdenAI (endpoint, provider, modèle, clé) sont lus depuis une
    CONFIG : celle passée en `config_id`, sinon la première config active du
    compte. On réutilise les mêmes réglages que le pipeline d'extraction plutôt
    qu'une variable d'environnement séparée.

    Renvoie {"fields": [...]} en succès, {"error": "..."} sinon (AJAX).
    """
    from flask import jsonify

    from ..doctype_generator import GenerationError, generate_fields_from_pdf

    file = request.files.get("pdf")
    if file is None or not file.filename:
        return jsonify({"error": "Aucun fichier PDF fourni."}), 400

    pdf_bytes = file.read()

    # Résolution des paramètres EdenAI depuis une config.
    from alambic_core.ai.edenai_endpoints import endpoint_for
    from alambic_core.ai.edenai_ocr import resolve_edenai_secret

    endpoint = ""
    secret_key = ""
    provider = ""
    model = ""

    config_id = request.form.get("config_id") or ""
    account_id = request.form.get("account_id") or current_user.account_id

    with _session() as s:
        cfg = None
        if config_id:
            cfg = s.get(Config, config_id)
        if cfg is None and account_id:
            # Première config active du compte (réglages EdenAI partagés).
            cfg = (
                s.query(Config)
                .filter(Config.account_id == account_id, Config.is_active.is_(True))
                .order_by(Config.config_name)
                .first()
            )
        if cfg is not None:
            settings = cfg.edenai_settings or {}
            region = settings.get("region", "") or "eu"
            endpoint = settings.get("extract_end_point", "") or endpoint_for("extract", region)
            provider = settings.get("extract_provider", "") or ""
            model = settings.get("extract_model", "") or ""
            secret_key = resolve_edenai_secret(cfg) or ""

    if not endpoint or not secret_key:
        return jsonify(
            {
                "error": (
                    "La génération par IA n'est pas configurée pour ce compte. "
                    "Vérifiez qu'une configuration active existe avec une clé EdenAI "
                    "et des réglages d'extraction renseignés, puis réessayez."
                )
            }
        ), 200

    try:
        kwargs = {"endpoint": endpoint, "secret_key": secret_key}
        if provider:
            kwargs["provider"] = provider
        if model:
            kwargs["model"] = model
        fields = generate_fields_from_pdf(pdf_bytes, **kwargs)
    except GenerationError as exc:
        return jsonify({"error": str(exc)}), 200  # 200 : message géré côté UI

    return jsonify({"fields": fields})


@doctypes_bp.route("/<doctype_id>/delete", methods=["POST"])
@admin_required
def delete_doctype(doctype_id: str):
    with _session() as s:
        dt = s.get(Doctype, doctype_id)
        if dt is None:
            abort(404)
        s.delete(dt)
        s.commit()
        flash("Doctype supprimé.", "success")
    return redirect(url_for("doctypes.list_doctypes"))
