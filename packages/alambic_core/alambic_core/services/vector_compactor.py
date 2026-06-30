"""alambic_core.services.vector_compactor — apprentissage incrémental des centroïdes.

Porté de FlowerScan (learning/vector/unit.py + batch.py), adapté à Garage.

Deux fonctions :
- append_vector_log : enregistre l'embedding d'un document validé dans un journal
  (__vector_logs__/<doctype>/<hash>.json). Appelé lors de la validation humaine
  (à brancher quand la brique validation/export existera).
- compact : lit les nouveaux logs depuis un curseur, met à jour les centroïdes
  par moyenne mobile normalisée (le centroïde le plus proche du nouveau vecteur
  est déplacé vers lui), et sauvegarde un nouveau modèle versionné promu en latest.

Le modèle produit est exactement celui que CategoryVectorStore charge au runtime.
Stockage Garage (work_bucket) :
- __vector_logs__/<doctype>/<hash>.json → {doctype, embedding, doc_id, timestamp}
- __vector_compactor__/state.json       → {last_processed_key, updated_at}
- __vectors_prod__/<version>.json + latest.json (cf. vector_store)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from alambic_core import storage

LOGS_PREFIX = "__vector_logs__"
MODEL_PREFIX = "__vectors_prod__"
STATE_KEY = "__vector_compactor__/state.json"


def _normalize(v):
    import numpy as np

    arr = np.array(v, dtype=np.float32)
    norm = np.linalg.norm(arr)
    return arr if norm == 0 else arr / norm


def append_vector_log(
    *, bucket: str | None, doctype: str, embedding: list[float], source_text: str
) -> str:
    """Enregistre un embedding de document validé dans le journal. Renvoie la clé.

    Idempotent : la clé dérive du hash du texte source, donc un même document
    validé deux fois n'écrit qu'une entrée.
    """
    bucket = bucket or storage.work_bucket()
    doc_id = hashlib.sha256(source_text.encode()).hexdigest()
    entry = {
        "doctype": doctype,
        "embedding": embedding,
        "doc_id": doc_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    key = f"{LOGS_PREFIX}/{doctype}/{doc_id}.json"
    storage.put_bytes(bucket, key, json.dumps(entry).encode())
    return key


def enrich_from_validation(config, document) -> str | None:
    """Enrichit le journal vectoriel depuis un document validé par un humain.

    Le doctype confirmé/corrigé par l'opérateur est une donnée d'apprentissage
    fiable : on génère l'embedding du texte du document et on l'ajoute au journal,
    ce qui affinera les centroïdes à la prochaine compaction (et réduira le
    recours au LLM de classification).

    Best-effort : toute erreur (embedder indisponible, texte vide…) est avalée
    pour ne JAMAIS faire échouer la validation. Renvoie la clé du log ou None.
    """
    try:
        doctype = (getattr(document, "doctype", "") or "").strip()
        text = (getattr(document, "ocr_markdown", "") or "").strip()
        # On n'apprend pas d'un type inconnu ou d'un texte vide.
        if not doctype or doctype == "unknown" or not text:
            return None

        from alambic_core.ai.embedder import EdenAIEmbedder, embedding_config_from_config

        embedder = EdenAIEmbedder(embedding_config_from_config(config))
        result = embedder.embedding(text)
        embedding = result.get("embedding") or []
        if not embedding:
            return None

        return append_vector_log(
            bucket=None, doctype=doctype, embedding=embedding, source_text=text
        )
    except Exception:  # noqa: BLE001
        # Best-effort : l'apprentissage ne doit jamais bloquer la validation.
        return None


def _load_model(bucket: str) -> dict:
    """Charge le modèle de centroïdes courant, ou un modèle vide (bootstrap)."""
    try:
        latest = json.loads(storage.get_bytes(bucket, f"{MODEL_PREFIX}/latest.json"))
        return json.loads(storage.get_bytes(bucket, latest["path"]))
    except Exception:  # noqa: BLE001
        return {"metadata": {"version": "v0"}, "vectors": {}}


def _load_state(bucket: str) -> dict:
    try:
        return json.loads(storage.get_bytes(bucket, STATE_KEY))
    except Exception:  # noqa: BLE001
        return {"last_processed_key": None}


def _save_state(bucket: str, last_key: str | None) -> None:
    state = {"last_processed_key": last_key, "updated_at": datetime.now(UTC).isoformat()}
    storage.put_bytes(bucket, STATE_KEY, json.dumps(state).encode())


def _load_new_logs(bucket: str, last_key: str | None):
    """Lit les logs de vecteurs postérieurs au curseur (ordre lexicographique)."""
    logs = []
    new_last_key = last_key
    for obj in storage.list_objects(bucket, prefix=LOGS_PREFIX):
        key = obj["Key"] if isinstance(obj, dict) else obj
        if not key.endswith(".json"):
            continue
        if last_key and key <= last_key:
            continue
        record = json.loads(storage.get_bytes(bucket, key))
        logs.append(record)
        if not new_last_key or key > new_last_key:
            new_last_key = key
    return logs, new_last_key


def _update_centroids(model: dict, logs: list) -> dict:
    """Met à jour les centroïdes par moyenne mobile normalisée.

    Pour chaque nouveau vecteur : trouve le centroïde le plus proche de son
    doctype et le déplace vers lui (somme normalisée). Première occurrence d'un
    doctype → crée le centroïde.
    """
    import numpy as np

    vectors = model.setdefault("vectors", {})
    for log in logs:
        label = log["doctype"]
        new_vec = _normalize(log["embedding"])
        if label not in vectors or not vectors[label]:
            vectors[label] = [new_vec.tolist() if hasattr(new_vec, "tolist") else list(new_vec)]
            continue
        centroids = vectors[label]
        best_idx, best_score = 0, -1.0
        for i, c in enumerate(centroids):
            score = float(np.array(c, dtype=np.float32) @ new_vec)
            if score > best_score:
                best_score, best_idx = score, i
        centroid = np.array(centroids[best_idx], dtype=np.float32)
        updated = _normalize(centroid + new_vec)
        centroids[best_idx] = updated.tolist()
    return model


def _save_model(bucket: str, model: dict) -> str:
    """Sauvegarde le modèle versionné et le promeut en latest. Renvoie la version."""
    version = datetime.now(UTC).strftime("v%Y%m%d%H%M%S")
    model.setdefault("metadata", {})
    model["metadata"]["version"] = version
    model["metadata"]["updated_at"] = datetime.now(UTC).isoformat()
    model["metadata"]["classes"] = sorted(model.get("vectors", {}).keys())

    key = f"{MODEL_PREFIX}/{version}.json"
    storage.put_bytes(bucket, key, json.dumps(model).encode())
    storage.put_bytes(
        bucket,
        f"{MODEL_PREFIX}/latest.json",
        json.dumps({"version": version, "path": key}).encode(),
    )
    return version


def compact(bucket: str | None = None) -> dict:
    """Exécute une passe de compaction. Renvoie un résumé {status, version, logs}."""
    bucket = bucket or storage.work_bucket()
    state = _load_state(bucket)
    last_key = state.get("last_processed_key")

    logs, new_last_key = _load_new_logs(bucket, last_key)
    if not logs:
        return {"status": "no_new_logs", "version": None, "logs": 0}

    model = _load_model(bucket)
    model = _update_centroids(model, logs)
    version = _save_model(bucket, model)
    _save_state(bucket, new_last_key)
    return {"status": "ok", "version": version, "logs": len(logs)}
