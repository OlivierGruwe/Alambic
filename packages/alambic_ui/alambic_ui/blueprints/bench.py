"""Blueprint bench — banc d'essai de robustesse depuis l'interface.

Lance le benchmark de robustesse (documents pathologiques) et affiche le
rapport. Réservé aux administrateurs. Le bench s'exécute en synchrone : il ne
touche pas la base ni le broker (il éprouve les briques de traitement sur des
documents générés), donc il est sûr à lancer depuis une requête web.

Le cas lourd « image géante » peut prendre plusieurs secondes ; on propose donc
un mode rapide (sans cas lourds) par défaut dans l'UI, avec une case pour le run
complet.
"""

from __future__ import annotations

from flask import Blueprint, render_template, request

from .auth import admin_required

bench_bp = Blueprint("bench", __name__, url_prefix="/bench")


@bench_bp.route("/", methods=["GET"])
@admin_required
def index():
    """Page du benchmark : formulaire + résultats si un run est demandé."""
    report = None
    ran = False
    include_heavy = request.args.get("heavy") == "1"

    if request.args.get("run") == "1":
        from alambic_core.tools.bench.robustness import run

        report = run(include_heavy=include_heavy)
        ran = True

    return render_template(
        "bench/index.html",
        report=report,
        ran=ran,
        include_heavy=include_heavy,
    )
