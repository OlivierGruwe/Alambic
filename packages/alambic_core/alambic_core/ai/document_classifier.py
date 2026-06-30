"""alambic_core.ai.document_classifier — orchestrateur de la cascade de classification.

Porté de FlowerScan (fcl_document_classifier). Classe un document via une cascade
à trois étages, du moins cher au plus cher :

1. LEXICAL : scoring par mots-clés. Si confiance ≥ seuil ET delta ≥ seuil → stop.
2. EMBEDDING : similarité du vecteur document vs centroïdes. Si score ≥ seuil ET
   delta ≥ seuil → stop.
3. LLM : fallback EdenAI. Seul capable de proposer un type inconnu (let_it_guess).

Chaque résultat est enrichi des champs du doctype identifié (pour l'extraction).
Le résultat contient {type, confidence, source, delta, fields, cost}.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    type: str = "unknown"
    description: str = ""
    confidence: float = 0.0
    source: str = ""
    delta: float = 0.0
    fields: list = field(default_factory=list)
    cost: float = 0.0


class DocumentClassifier:
    """Cascade lexical → embedding → LLM."""

    def __init__(
        self,
        *,
        lexical_engine,
        embedder,
        llm,
        vector_store,
        category_registry,
        threshold: float = 0.75,
        min_delta: float = 0.10,
        lexical_threshold: float = 0.6,
        lexical_delta: float = 0.20,
    ):
        self.lexical = lexical_engine
        self.embedder = embedder
        self.llm = llm
        self.vector_store = vector_store
        self.registry = category_registry
        self.threshold = threshold
        self.min_delta = min_delta
        self.lexical_threshold = lexical_threshold
        self.lexical_delta = lexical_delta
        if hasattr(self.llm, "set_registry"):
            self.llm.set_registry(self.registry)

    def _enrich_with_fields(self, result: ClassificationResult) -> ClassificationResult:
        """Ajoute les champs du doctype identifié (pour l'extraction)."""
        if not result.fields:
            doctype = self.registry.get_doctype(result.type)
            if doctype is not None:
                if isinstance(doctype, dict):
                    result.fields = doctype.get("fields", []) or []
                else:
                    result.fields = getattr(doctype, "fields", []) or []
        return result

    def classify_document(self, text: str) -> ClassificationResult:
        """Classe le texte via la cascade. Renvoie un ClassificationResult."""
        # ── 1. LEXICAL ──────────────────────────────────────────────
        lex_label, lex_conf, lex_delta = self.lexical.lexical_scoring(text)
        if lex_label and lex_conf >= self.lexical_threshold and lex_delta >= self.lexical_delta:
            return self._enrich_with_fields(
                ClassificationResult(
                    type=str(lex_label),
                    confidence=round(lex_conf, 4),
                    source=f"lexical_v{self.lexical.version}",
                    delta=round(lex_delta, 4),
                )
            )

        # ── 2. EMBEDDING ────────────────────────────────────────────
        # Un échec d'embedding (endpoint indisponible, provider en panne) ne doit
        # pas casser la classification : on dégrade vers le LLM (cascade résiliente).
        try:
            embed_results = self.embedder.embed_document(text)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding indisponible, bascule sur le LLM : %s", exc)
            embed_results = []
        if not embed_results:
            return self._fallback_llm(text, 0.0)

        best_label, best_score, best_delta = None, 0.0, 0.0
        for r in embed_results:
            label, score, delta = self.vector_store.score(r["embedding"])
            if score > best_score:
                best_label, best_score, best_delta = label, score, delta

        if best_label and best_score >= self.threshold and best_delta >= self.min_delta:
            return self._enrich_with_fields(
                ClassificationResult(
                    type=best_label,
                    confidence=round(best_score, 4),
                    source=f"embedding_v{self.vector_store.prod_version}",
                    delta=round(best_delta, 4),
                )
            )

        # ── 3. LLM (fallback) ───────────────────────────────────────
        return self._fallback_llm(text, best_delta)

    def _fallback_llm(self, text: str, delta: float) -> ClassificationResult:
        llm_result, cost = self.llm.classify(text)
        return self._enrich_with_fields(
            ClassificationResult(
                type=llm_result.get("type", "unknown") or "unknown",
                description=llm_result.get("description", ""),
                fields=llm_result.get("fields", []) or [],
                confidence=float(llm_result.get("confidence", 0.0) or 0.0),
                source=f"llm_v{self.registry.version}",
                delta=round(delta, 4),
                cost=cost,
            )
        )
