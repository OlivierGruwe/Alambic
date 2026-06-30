"""alambic_core.pipeline.steps — étapes ordonnées du pipeline de traitement.

L'ordre sert au skip de rejouabilité : si un document/une transaction est déjà
au-delà d'une étape, ré-exécuter cette étape est sauté (idempotence). L'ordre
reprend la séquence FlowerScan (PROCESS_LABELS).

Tolérant : une étape inconnue (ajout futur, valeur hors liste) renvoie un rang
None → jamais sautée (on exécute par défaut, on ne bloque pas).
"""

from __future__ import annotations

# Séquence canonique des process du pipeline, du plus tôt au plus tard.
# Les libellés correspondent à PROCESS_LABELS côté UI (suivi des transactions).
PIPELINE_STEPS: list[str] = [
    # Ingestion
    "NEWDOC",
    "DOC_CREATED",
    "FILEEXTRACTOR",
    "DOC_EXTRACTED",
    # Conversion
    "DETECT_FILE_TYPE",
    "UNLOCK_PDF",
    "OFFICE_CONVERTER",
    "IMAGECONVERTER",
    "TEXTCONVERTER",
    "FILE_CONVERTED",
    # Lecture (OCR + codes-barres) sur le document entier
    "PDF_TRUNCATED",
    "CAB_READER",
    "OCR_READER",
    "OCR_DONE",
    # Détection de découpage / split en documents logiques
    "DETECT_MULTI_DOC",
    "DOC_SPLITTER",
    # Dispatch IA
    "AI_DISPATCHED",
    "DISPATCH_DONE",
    # Traitement IA par document
    "AI_PROCESSING",
    "CLASSIFIER",
    "FIELD_EXTRACTOR",
    "DATA_EXTRACTED",
    # Fin
    "VALIDATED",
    "EXPORTED",
]

# Index pour lookup O(1) du rang d'une étape.
_STEP_RANK: dict[str, int] = {name: i for i, name in enumerate(PIPELINE_STEPS)}


def step_rank(process: str) -> int | None:
    """Rang d'une étape dans la séquence, ou None si inconnue (jamais sautée)."""
    return _STEP_RANK.get(process)


def is_already_past(current_process: str, target_process: str) -> bool:
    """True si current_process est strictement au-delà de target_process.

    Sert au skip : si l'entité est déjà plus avancée que l'étape qu'on s'apprête
    à (ré)exécuter, on saute. Tolérant : si l'une des deux étapes est inconnue,
    on ne saute pas (retourne False) — on préfère exécuter que bloquer à tort.
    """
    cur = step_rank(current_process)
    tgt = step_rank(target_process)
    if cur is None or tgt is None:
        return False
    return cur > tgt
