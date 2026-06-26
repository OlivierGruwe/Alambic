"""Formulaires WTForms de l'interface d'administration."""

from __future__ import annotations

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    HiddenField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, EqualTo, Length, Optional


class LoginForm(FlaskForm):
    # Pas de validateur Email() ici : au login on compare l'identifiant à la base,
    # inutile d'imposer un format RFC (et d'ajouter la dépendance email_validator).
    email = StringField("Email", validators=[DataRequired()])
    password = PasswordField("Mot de passe", validators=[DataRequired()])
    submit = SubmitField("Se connecter")


class AccountForm(FlaskForm):
    """Création / édition d'un compte client."""

    id = HiddenField()
    account_name = StringField("Nom du compte", validators=[DataRequired(), Length(max=255)])
    active = BooleanField("Actif", default=True)

    # Adresse (5 lignes → bloc JSONB address.line1..line5)
    address1 = StringField("Adresse (ligne 1)", validators=[Optional(), Length(max=255)])
    address2 = StringField("Adresse (ligne 2)", validators=[Optional(), Length(max=255)])
    address3 = StringField("Adresse (ligne 3)", validators=[Optional(), Length(max=255)])
    address4 = StringField("Adresse (ligne 4)", validators=[Optional(), Length(max=255)])
    address5 = StringField("Adresse (ligne 5)", validators=[Optional(), Length(max=255)])
    zip = StringField("Code postal", validators=[Optional(), Length(max=20)])
    town = StringField("Ville", validators=[Optional(), Length(max=255)])
    country = StringField("Pays", validators=[Optional(), Length(max=100)])

    # Allowlist anti-SSRF : domaines autorisés pour l'enrichissement (chaîne,
    # séparés par virgules/points-virgules). Vide = aucun WS autorisé (fail-closed).
    enrich_allowed_domains = TextAreaField(
        "Domaines d'enrichissement autorisés",
        validators=[Optional(), Length(max=2048)],
        description="Séparés par des virgules. Vide = aucun web service autorisé.",
    )

    # Secret EdenAI partagé du compte. Champ masqué : laissé vide = inchangé,
    # renseigné = remplace la valeur existante. Jamais réaffiché en clair.
    edenai_secret_key = PasswordField(
        "Clé EdenAI du compte",
        validators=[Optional()],
        description="Laissez vide pour conserver la clé actuelle.",
    )

    submit = SubmitField("Enregistrer")


class UserForm(FlaskForm):
    """Création / édition d'un utilisateur (sans mot de passe : via invitation)."""

    id = HiddenField()
    email = StringField("Email", validators=[DataRequired(), Length(max=320)])
    full_name = StringField("Nom complet", validators=[Optional(), Length(max=255)])
    role = SelectField(
        "Rôle",
        choices=[
            ("VALIDATOR", "Valideur"),
            ("ADMIN", "Administrateur"),
            ("SUPER_ADMIN", "Super-administrateur"),
        ],
        validators=[DataRequired()],
    )
    account_id = SelectField("Compte", validators=[Optional()], choices=[])
    active = BooleanField("Actif", default=True)
    submit = SubmitField("Enregistrer")


class ConfigForm(FlaskForm):
    """Métadonnées d'une config. Le détail (blocs, secrets) est géré via
    config_schema à partir des champs POST bruts."""

    id = HiddenField()
    config_name = StringField(
        "Nom de la configuration", validators=[DataRequired(), Length(max=255)]
    )
    account_id = SelectField("Compte", validators=[Optional()], choices=[])
    doctype_id = SelectField("Doctype", validators=[Optional()], choices=[])
    need_validation = BooleanField("Validation requise", default=True)
    multi_doc_detect = BooleanField("Détection multi-documents", default=False)
    submit = SubmitField("Enregistrer")


class DoctypeForm(FlaskForm):
    """Métadonnées d'un doctype. Les champs d'extraction sont gérés à part
    (formulaire dynamique sérialisé en json_content)."""

    id = HiddenField()
    doctype_name = StringField("Nom du doctype", validators=[DataRequired(), Length(max=255)])
    is_public = BooleanField("Public (tous les comptes)", default=False)
    account_id = SelectField("Compte", validators=[Optional()], choices=[])
    submit = SubmitField("Enregistrer")


class AcceptInviteForm(FlaskForm):
    """Définition du mot de passe par l'utilisateur invité."""

    password = PasswordField(
        "Mot de passe",
        validators=[DataRequired(), Length(min=8, message="8 caractères minimum.")],
    )
    confirm = PasswordField(
        "Confirmer le mot de passe",
        validators=[DataRequired(), EqualTo("password", message="Les mots de passe diffèrent.")],
    )
    submit = SubmitField("Définir mon mot de passe")
