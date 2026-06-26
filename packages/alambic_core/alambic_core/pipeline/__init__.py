"""alambic_core.pipeline — socle d'exécution du pipeline de traitement.

Expose le mécanisme `step` (exécution encadrée d'une étape : MAJ DB, journal,
durée, rejouabilité) et la définition ordonnée des étapes.
"""

from .step import StepContext, step
from .steps import PIPELINE_STEPS, is_already_past, step_rank

__all__ = ["step", "StepContext", "PIPELINE_STEPS", "step_rank", "is_already_past"]
