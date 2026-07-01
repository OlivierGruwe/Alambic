"""alambic_core.ai.cascade_ocr — OCR en cascade Tesseract → EdenAI.

Stratégie économique : on tente d'abord Tesseract (local, gratuit) avec
prétraitement. Si le résultat est de qualité suffisante, on le garde. Sinon
(document vraiment difficile), on bascule sur EdenAI (payant, plus robuste).

EdenAI n'est donc appelé que sur les documents où Tesseract échoue, ce qui
minimise le coût tout en garantissant un résultat sur les cas durs.

Expose le contrat habituel ocr_bytes(data, filename) -> OcrResult, donc
interchangeable dans PdfExtractor.
"""

from __future__ import annotations

import logging

from alambic_core.ai.edenai_ocr import OcrResult

logger = logging.getLogger("alambic.ai.cascade")

# Seuil de score en dessous duquel le résultat Tesseract est jugé insuffisant et
# déclenche le repli EdenAI. Le score est la confiance cumulée des mots fiables
# (cf. image_preprocess.score_ocr_dict) ; ce seuil correspond à « très peu de
# texte fiable détecté » (calibré : un document lisible dépasse largement).
DEFAULT_MIN_SCORE = 500.0


class CascadeOcr:
    """OCR Tesseract d'abord, EdenAI en secours si le score est trop faible."""

    provider = "cascade"

    def __init__(self, tesseract, edenai, *, min_score: float = DEFAULT_MIN_SCORE):
        self.tesseract = tesseract
        self.edenai = edenai
        self.min_score = min_score

    def _score(self, result: OcrResult) -> float:
        """Score d'un OcrResult : approxime la confiance cumulée via le volume de
        texte fiable. On se base sur les lignes retournées (Tesseract fournit des
        lignes filtrées) : longueur cumulée du texte, proxy simple et robuste."""
        if not result or not result.lines:
            return 0.0
        return float(sum(len(ln.get("text") or "") for ln in result.lines))

    def ocr_bytes(self, data: bytes, filename: str = "") -> OcrResult:
        # 1. Tentative Tesseract (local, gratuit).
        try:
            tess_res = self.tesseract.ocr_bytes(data, filename)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cascade : Tesseract a échoué (%s), repli EdenAI", exc)
            tess_res = OcrResult(provider="tesseract")

        score = self._score(tess_res)
        if score >= self.min_score:
            logger.debug("Cascade : Tesseract suffisant (score %.0f)", score)
            return tess_res

        # 2. Repli EdenAI (payant) sur document difficile.
        logger.info(
            "Cascade : Tesseract faible (score %.0f < %.0f), repli EdenAI pour %s",
            score, self.min_score, filename,
        )
        try:
            eden_res = self.edenai.ocr_bytes(data, filename)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cascade : EdenAI a aussi échoué (%s), on garde Tesseract", exc)
            return tess_res

        # On garde EdenAI seulement s'il fait mieux (sinon Tesseract reste).
        if self._score(eden_res) >= score:
            return eden_res
        return tess_res
