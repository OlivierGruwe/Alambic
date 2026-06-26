"""
Blueprint invite — acceptation d'invitation (page publique, sans login).

L'utilisateur invité accède à /invitation/<token>, définit son mot de passe, et
peut ensuite se connecter. Le jeton est à usage unique et expire.
"""

from __future__ import annotations

from alambic_core.db.session import get_sessionmaker
from alambic_core.security.invitations import accept_invitation, find_valid_invitation
from flask import Blueprint, flash, redirect, render_template, url_for

from ..forms import AcceptInviteForm

invite_bp = Blueprint("invite", __name__)


def _session():
    return get_sessionmaker()()


@invite_bp.route("/invitation/<token>", methods=["GET", "POST"])
def accept_invite(token: str):
    with _session() as s:
        user = find_valid_invitation(s, token)
        if user is None:
            return render_template("invite/invalid.html"), 404

        form = AcceptInviteForm()
        if form.validate_on_submit():
            accept_invitation(s, token, form.password.data)
            flash("Votre mot de passe est défini. Vous pouvez vous connecter.", "success")
            return redirect(url_for("auth.login"))

        email = user.email
        s.expunge_all()
    return render_template("invite/accept.html", form=form, email=email, token=token)
