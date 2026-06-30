"""alambic_core.ai.vector_store — magasin de centroïdes pour la classification.

Porté de FlowerScan (fcl_category_vector_store), adapté à Garage (S3 souverain)
au lieu d'AWS S3. Charge les centroïdes de catégories (vecteurs représentatifs
par doctype) depuis Garage et expose une matrice numpy pour le scoring par
similarité cosinus.

Stockage Garage (work_bucket) :
- __vectors_prod__/latest.json   → {"version": ..., "path": "__vectors_prod__/<v>.json"}
- __vectors_prod__/<version>.json → {"metadata": {...}, "vectors": {label: [[...], ...]}}

Le modèle est alimenté par la compaction incrémentale (voir services/vector_compactor)
qui agrège les vecteurs issus des validations humaines.
"""

from __future__ import annotations

import json
import threading
import time

from alambic_core import storage

PROD_PREFIX = "__vectors_prod__"


def _normalize_np(v):
    import numpy as np

    arr = np.asarray(v, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return arr if norm == 0 else arr / norm


class CategoryVectorStore:
    """Charge et sert les centroïdes de catégories depuis Garage."""

    def __init__(
        self,
        *,
        bucket: str | None = None,
        prod_prefix: str = PROD_PREFIX,
        reload_interval_seconds: int = 300,
    ):
        self.bucket = bucket or storage.work_bucket()
        self.prod_prefix = prod_prefix
        self.reload_interval = reload_interval_seconds

        self.prod_matrix = None
        self.label_index: list[str] = []
        self.prod_version = "uninitialized"

        self._last_check = 0.0
        self._lock = threading.Lock()
        self._load_prod()

    @property
    def categories(self) -> list[str]:
        return sorted(set(self.label_index))

    def check_reload(self) -> None:
        """Recharge périodiquement les centroïdes (modèle mis à jour en fond)."""
        if (time.time() - self._last_check) <= self.reload_interval:
            return
        with self._lock:
            if (time.time() - self._last_check) <= self.reload_interval:
                return
            self._load_prod()
            self._last_check = time.time()

    def _load_prod(self) -> None:
        matrix, label_index, version = self._load_prefix(self.prod_prefix)
        self.prod_matrix = matrix
        self.label_index = label_index
        self.prod_version = version

    def _load_prefix(self, prefix: str):
        """Charge le modèle pointé par <prefix>/latest.json. Tolérant à l'absence."""
        try:
            latest_raw = storage.get_bytes(self.bucket, f"{prefix}/latest.json")
        except Exception:  # noqa: BLE001 — pas encore de modèle (bootstrap)
            return None, [], "bootstrap"
        try:
            latest = json.loads(latest_raw)
            model_raw = storage.get_bytes(self.bucket, latest["path"])
            model = json.loads(model_raw)
            matrix, label_index = self._build_global_matrix(model.get("vectors", {}))
            return matrix, label_index, latest.get("version", "unknown")
        except Exception:  # noqa: BLE001
            return None, [], "error"

    def _build_global_matrix(self, vectors_dict: dict):
        """Empile tous les centroïdes en une matrice (lignes) + index de labels."""
        import numpy as np

        centroids = []
        label_index = []
        for label, centroid_list in vectors_dict.items():
            for centroid in centroid_list:
                centroids.append(centroid)
                label_index.append(label)
        if not centroids:
            return None, []
        return np.array(centroids, dtype=np.float32), label_index

    def score(self, doc_vector) -> tuple[str | None, float, float]:
        """Score un vecteur document contre les centroïdes.

        Renvoie (meilleur_label, meilleur_score, delta) où delta est l'écart
        entre le meilleur et le 2e meilleur label distinct (mesure de netteté).
        """

        self.check_reload()
        if self.prod_matrix is None or not self.label_index:
            return None, 0.0, 0.0

        vec = _normalize_np(doc_vector)
        sims = self.prod_matrix @ vec  # similarité cosinus (tout est normalisé)

        # Meilleur score par label.
        best_by_label: dict[str, float] = {}
        for label, sim in zip(self.label_index, sims, strict=False):
            s = float(sim)
            if label not in best_by_label or s > best_by_label[label]:
                best_by_label[label] = s

        ranked = sorted(best_by_label.items(), key=lambda kv: kv[1], reverse=True)
        best_label, best_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        delta = best_score - second_score
        return best_label, best_score, delta
