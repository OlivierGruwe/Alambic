"""alambic_core.services.auto_validation — décision de validation d'un document.

En fin d'extraction, un document peut soit partir en validation humaine, soit
être validé automatiquement. La règle :

  - need_validation activé sur la config → validation humaine TOUJOURS obligatoire
    (PENDING_VALIDATION), quelle que soit la confiance.
  - need_validation désactivé → validation AUTOMATIQUE (VALIDATED), SAUF si au
    moins un champ extrait a un indice de confiance strictement inférieur au
    seuil auto_validation_threshold : dans ce cas, par sécurité, on repasse en
    validation humaine (PENDING_VALIDATION).

Le seuil et need_validation sont lus sur la config. Le seuil vit dans le bloc
general (auto_validation_threshold) ; need_validation est une colonne.
"""

from __future__ import annotations

from alambic_core.domain.enums import DocumentStatus


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def decide_validation_status(config, indexes: list) -> str:
    """Renvoie le statut de validation à poser : VALIDATED ou PENDING_VALIDATION.

    `indexes` est la liste des index extraits, chacun avec un éventuel
    `index_score` (chaîne ou nombre) représentant l'indice de confiance du champ.
    """
    # 1. need_validation → humain obligatoire.
    if bool(getattr(config, "need_validation", True)):
        return DocumentStatus.PENDING_VALIDATION.value

    # 2. Validation auto, sauf si un champ est sous le seuil de confiance.
    general = getattr(config, "general", None) or {}
    threshold = _to_float(general.get("auto_validation_threshold"), 0.0)

    # Seuil à 0 (ou absent) → aucun plancher de confiance : tout passe en auto.
    if threshold <= 0:
        return DocumentStatus.VALIDATED.value

    for idx in indexes or []:
        raw_score = idx.get("index_score") if isinstance(idx, dict) else None
        # Un champ sans score n'est pas considéré comme douteux (rien à comparer).
        if raw_score is None or raw_score == "":
            continue
        score = _to_float(raw_score, default=1.0)
        if score < threshold:
            # Au moins un champ douteux → validation humaine par sécurité.
            return DocumentStatus.PENDING_VALIDATION.value

    return DocumentStatus.VALIDATED.value
