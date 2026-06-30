"""
alambic_ui — application factory.

Assemble l'interface d'administration Flask et la branche sur le socle souverain
alambic_core : authentification via LocalAuthProvider (table users + argon2),
données via les repositories. Aucune dépendance AWS.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker, init_core
from alambic_core.security.auth import LocalAuthProvider
from flask import Flask, redirect, url_for
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect

from .config import Config

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message = "Veuillez vous connecter pour accéder à cette page."

csrf = CSRFProtect()

# Fournisseur d'authentification : aujourd'hui local, demain Keycloak (même API).
_auth_provider: LocalAuthProvider | None = None


def get_auth_provider() -> LocalAuthProvider:
    assert _auth_provider is not None, "auth provider non initialisé"
    return _auth_provider


class FlaskUser:
    """Adaptateur Flask-Login autour du User d'alambic_core.

    Flask-Login attend is_authenticated / is_active / get_id. On enveloppe le
    modèle métier plutôt que de le polluer avec des détails de framework web.
    """

    def __init__(self, user):
        self._user = user

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_active(self) -> bool:
        return self._user.active

    @property
    def is_anonymous(self) -> bool:
        return False

    def get_id(self) -> str:
        return self._user.id

    # Expose les attributs métier (email, role, is_admin…) de façon transparente.
    def __getattr__(self, name):
        return getattr(self._user, name)


def create_app(config_object: type[Config] = Config) -> Flask:
    app = Flask(__name__)
    app.config.from_object(config_object)

    # Socle souverain : connexion DB + chiffrement.
    init_core()
    session_factory = get_sessionmaker()

    global _auth_provider
    _auth_provider = LocalAuthProvider(_session_scope_factory(session_factory))

    login_manager.init_app(app)
    csrf.init_app(app)

    @login_manager.user_loader
    def load_user(user_id: str):
        user = get_auth_provider().get_user(user_id)
        return FlaskUser(user) if user is not None else None

    # Blueprints
    from .blueprints.accounts import accounts_bp
    from .blueprints.auth import auth_bp
    from .blueprints.configs import configs_bp
    from .blueprints.dashboard import dashboard_bp
    from .blueprints.doctypes import doctypes_bp
    from .blueprints.invite import invite_bp
    from .blueprints.mail_configs import mail_configs_bp
    from .blueprints.transactions import transactions_bp
    from .blueprints.users import users_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(accounts_bp)
    app.register_blueprint(users_bp)
    app.register_blueprint(doctypes_bp)
    app.register_blueprint(configs_bp)
    app.register_blueprint(mail_configs_bp)
    app.register_blueprint(transactions_bp)
    app.register_blueprint(invite_bp)

    @app.route("/")
    def index():
        return redirect(url_for("dashboard.index"))

    return app


def _session_scope_factory(sessionmaker_):
    """Adapte un sessionmaker en factory de context manager pour le provider."""
    from contextlib import contextmanager

    @contextmanager
    def scope():
        s = sessionmaker_()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return scope
