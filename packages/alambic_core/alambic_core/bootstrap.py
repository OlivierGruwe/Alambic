"""
alambic_core.bootstrap — initialisation d'un déploiement neuf.

Crée le PREMIER super-administrateur, de façon interactive et sécurisée. C'est
la résolution du problème de l'œuf et la poule : il faut un admin pour créer des
utilisateurs, mais au premier déploiement il n'y en a aucun.

Idempotent : si un super-admin existe déjà, le script refuse d'en créer un autre
(on ne veut pas de création accidentelle). Pour réinitialiser, passer par l'UI.

Usage (infra debout, migrations appliquées) :
    uv run python -m alambic_core.bootstrap
ou via Makefile :
    make bootstrap

Le mot de passe est saisi en masqué (getpass), avec confirmation. Jamais affiché,
jamais stocké en clair : seul son hash argon2 est persisté.
"""

from __future__ import annotations

import getpass
import sys

from .db.session import get_sessionmaker, init_core
from .domain.enums import UserRole
from .models import User
from .repositories import UserRepository
from .security.passwords import hash_password


def _prompt_email() -> str:
    email = input("Email du super-admin : ").strip().lower()
    if "@" not in email or len(email) < 5:
        print("  ✗ Email invalide.", file=sys.stderr)
        sys.exit(1)
    return email


def _prompt_password() -> str:
    pwd = getpass.getpass("Mot de passe : ")
    if len(pwd) < 8:
        print("  ✗ Le mot de passe doit faire au moins 8 caractères.", file=sys.stderr)
        sys.exit(1)
    confirm = getpass.getpass("Confirmer le mot de passe : ")
    if pwd != confirm:
        print("  ✗ Les mots de passe ne correspondent pas.", file=sys.stderr)
        sys.exit(1)
    return pwd


def main() -> None:
    init_core()
    Sess = get_sessionmaker()

    with Sess() as session:
        repo = UserRepository(session)

        # Idempotence : on ne crée pas un 2e super-admin par accident.
        if repo.has_any_super_admin():
            print(
                "Un super-admin existe déjà. Bootstrap ignoré.\n"
                "Pour gérer les utilisateurs, passez par l'interface d'administration."
            )
            return

        print("=== Initialisation Alambic : création du premier super-admin ===")
        email = _prompt_email()

        # Garde-fou : email déjà pris (par un non-super-admin par ex.)
        if repo.by_email(email) is not None:
            print(f"  ✗ Un utilisateur existe déjà avec l'email {email}.", file=sys.stderr)
            sys.exit(1)

        full_name = input("Nom complet (optionnel) : ").strip()
        password = _prompt_password()

        user = User(
            email=email,
            full_name=full_name,
            password_hash=hash_password(password),
            role=UserRole.SUPER_ADMIN.value,
            account_id=None,  # super-admin transverse
            active=True,
            auth_provider="local",
        )
        session.add(user)
        session.commit()
        print(f"\n✓ Super-admin créé : {email}")
        print("  Vous pouvez maintenant vous connecter à l'interface d'administration.")


if __name__ == "__main__":
    main()
