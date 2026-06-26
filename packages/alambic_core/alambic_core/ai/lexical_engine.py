"""alambic_core.ai.lexical_engine — scoring lexical rapide (1er étage de la cascade).

Porté de FlowerScan (fcl_lexical_engine), adapté à Garage (au lieu de DynamoDB +
S3). Premier étage, le moins cher : un modèle de poids `mot → {label: poids}`
(log-odds façon Naive Bayes) qui score un texte par somme des poids des mots
présents.

PALIER 1 : la structure runtime est complète, mais le modèle est vide tant qu'il
n'a pas été entraîné. `lexical_scoring` renvoie alors (None, 0, 0) → la cascade
passe à l'embedding, ce qui est le comportement correct. `update_stats` accumule
les comptes (mot, classe) sur Garage. Le rebuild du modèle de poids et son
portage sur PostgreSQL (stats relationnelles) sont prévus au palier 2.

Stockage Garage (work_bucket) :
- __lexical_prod__/latest.json   → {"version", "path"}
- __lexical_prod__/<version>.json → {"metadata": {"classes": [...]}, "weights": {word: {label: w}}}
- __lexical_stats__/class/<label>.json → {"total_docs": N}
- __lexical_stats__/word/<label>/<word>.json → {"count": N}
"""

from __future__ import annotations

import json
import re
import threading
import time

from alambic_core import storage

PROD_PREFIX = "__lexical_prod__"
STATS_PREFIX = "__lexical_stats__"
WORD_PATTERN = re.compile(r"[a-zàâäéèêëïîôöùûüç0-9]{3,}", re.I)


class LexicalEngine:
    """Scoring lexical par poids mot→label, avec apprentissage incrémental."""

    def __init__(self, *, bucket: str | None = None, reload_interval_seconds: int = 300):
        self.bucket = bucket or storage.work_bucket()
        self.reload_interval = reload_interval_seconds
        self._model: dict | None = None
        self._metadata: dict = {}
        self._version = "uninitialized"
        self._last_check = 0.0
        self._lock = threading.Lock()
        self._load_latest_model()

    @property
    def categories(self) -> list[str]:
        if self._metadata and "classes" in self._metadata:
            return list(self._metadata["classes"])
        return []

    @property
    def version(self) -> str:
        return self._version

    def _check_and_reload_if_needed(self) -> None:
        if (time.time() - self._last_check) <= self.reload_interval:
            return
        with self._lock:
            if (time.time() - self._last_check) <= self.reload_interval:
                return
            self._load_latest_model()
            self._last_check = time.time()

    def _load_latest_model(self) -> None:
        """Charge le modèle de poids depuis Garage (tolérant à l'absence)."""
        try:
            latest = json.loads(storage.get_bytes(self.bucket, f"{PROD_PREFIX}/latest.json"))
            model_doc = json.loads(storage.get_bytes(self.bucket, latest["path"]))
            self._model = model_doc.get("weights", {})
            self._metadata = model_doc.get("metadata", {})
            self._version = latest.get("version", "unknown")
        except Exception:  # noqa: BLE001 — pas encore entraîné (bootstrap)
            self._model = None
            self._metadata = {}
            self._version = "bootstrap"

    def lexical_scoring(self, text: str) -> tuple[str | None, float, float]:
        """Score un texte. Renvoie (label, confiance, delta).

        (None, 0, 0) si pas de modèle entraîné ou aucun mot connu → la cascade
        passe à l'étage suivant (embedding).
        """
        self._check_and_reload_if_needed()
        if not self._model:
            return None, 0.0, 0.0

        words = set(WORD_PATTERN.findall(text.lower()))
        scores: dict[str, float] = {}
        for word in words:
            word_weights = self._model.get(word)
            if word_weights:
                for label, weight in word_weights.items():
                    scores[label] = scores.get(label, 0.0) + weight

        if not scores:
            return None, 0.0, 0.0

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_label, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        delta = top_score - second_score
        confidence = min(1.0, abs(top_score) / 10.0)
        return top_label, confidence, delta

    def update_stats(self, text: str, label: str) -> None:
        """Accumule les comptes (mot, classe) sur Garage. Ne casse jamais le pipeline."""
        if not text or not label:
            return
        words = set(WORD_PATTERN.findall(text.lower()))
        try:
            # Compte de documents de la classe.
            class_key = f"{STATS_PREFIX}/class/{label}.json"
            try:
                total = json.loads(storage.get_bytes(self.bucket, class_key)).get("total_docs", 0)
            except Exception:  # noqa: BLE001
                total = 0
            storage.put_bytes(
                self.bucket, class_key, json.dumps({"total_docs": total + 1}).encode()
            )

            # Compte par (mot, classe).
            for word in words:
                word_key = f"{STATS_PREFIX}/word/{label}/{word}.json"
                try:
                    count = json.loads(storage.get_bytes(self.bucket, word_key)).get("count", 0)
                except Exception:  # noqa: BLE001
                    count = 0
                storage.put_bytes(self.bucket, word_key, json.dumps({"count": count + 1}).encode())
        except Exception:  # noqa: BLE001 — ne jamais bloquer la classification
            pass
