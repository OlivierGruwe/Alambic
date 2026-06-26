"""alambic_core.ai.embedder — calcul d'embeddings de texte via EdenAI.

Porté de FlowerScan (fcl_edenia_embedder). Transforme un texte en vecteur(s)
normalisé(s), utilisés par la classification par similarité (vector store).

Pipeline : nettoyage → normalisation structurelle → chunking token-aware
(tiktoken) → appel EdenAI embeddings → normalisation L2 des vecteurs.

La config réseau (provider, endpoint, clé) vient de EmbeddingConfig, construit
depuis une Config Alambic (edenai_settings + edenai_secret_enc).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

DEFAULT_EMBEDDING_MODEL = "bge-m3"


@dataclass
class EmbeddingConfig:
    secret_key: str
    endpoint: str
    provider: str
    model: str = DEFAULT_EMBEDDING_MODEL
    max_chars: int = 12000
    account_id: str = ""
    timeout: int = 30
    max_retries: int = 3


def embedding_config_from_config(config) -> EmbeddingConfig:
    """Construit une EmbeddingConfig pour l'embedder LOCAL (souverain).

    L'embedding passe exclusivement par le service local TEI (BGE-M3) de la stack
    Docker : aucune donnée ne sort de l'infra, pas de clé API, pas de provider tiers.
    L'URL du service est lue dans EMBEDDING_LOCAL_URL (ex.
    http://embeddings:80/v1/embeddings). Si elle est vide, l'endpoint l'est aussi
    et la cascade de classification saute simplement l'étage embedding (dégradation
    gracieuse vers le LLM).
    """
    import os

    settings = config.edenai_settings or {}
    local_url = os.environ.get("EMBEDDING_LOCAL_URL", "").strip()
    model = os.environ.get("EMBEDDING_LOCAL_MODEL", "bge-m3")

    return EmbeddingConfig(
        secret_key="",
        endpoint=local_url,
        provider=model,
        model=model,
        max_chars=int(settings.get("embedding_max_chars", 12000) or 12000),
        account_id=config.account_id or "",
    )


# ── Préparation du texte ────────────────────────────────────────────────


def clean_text(text: str) -> str:
    """Normalisation Unicode + espaces."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def structural_normalize(text: str) -> str:
    """Retire les marqueurs [PAGE N] internes (bruit pour l'embedding)."""
    return re.sub(r"\[PAGE\s+\d+\]", " ", text)


def _smart_truncate(text: str, max_chars: int) -> str:
    """Tronque en coupant à une frontière de phrase/ligne proche."""
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_dot = truncated.rfind(".")
    last_newline = truncated.rfind("\n")
    cut = max(last_dot, last_newline)
    if cut > max_chars * 0.6:
        return truncated[:cut]
    return truncated


def _normalize_vector(vec: list[float]) -> list[float]:
    """Normalisation L2 (vecteur unitaire) pour la similarité cosinus."""
    import numpy as np

    arr = np.array(vec, dtype=np.float32)
    norm = np.linalg.norm(arr)
    if norm == 0:
        return arr.tolist()
    return (arr / norm).tolist()


class EdenAIEmbedder:
    """Client d'embedding EdenAI. Renvoie des vecteurs normalisés."""

    def __init__(self, config: EmbeddingConfig):
        self.config = config
        self.total_cost = 0.0

    def _chunk_text(self, text: str, max_tokens: int = 800, overlap: int = 120) -> list[str]:
        """Découpe le texte en chunks token-aware (tiktoken si dispo, sinon chars)."""
        try:
            import tiktoken

            try:
                tokenizer = tiktoken.encoding_for_model(self.config.model)
            except KeyError:
                tokenizer = tiktoken.get_encoding("cl100k_base")
            tokens = tokenizer.encode(text)
            if not tokens:
                return []
            chunks = []
            start = 0
            while start < len(tokens):
                chunk_tokens = tokens[start : start + max_tokens]
                chunks.append(tokenizer.decode(chunk_tokens))
                start += max_tokens - overlap
            return chunks
        except ImportError:
            # Fallback sans tiktoken : découpe approximative par caractères.
            approx = max_tokens * 4
            return [text[i : i + approx] for i in range(0, len(text), approx)] or [text]

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Appelle l'API EdenAI embeddings, renvoie un vecteur par texte.

        Compatible avec deux formats d'API EdenAI :
        - v3 (OpenAI-compatible) : payload {"model", "input"}, réponse {"data": [{"embedding"}]}.
        - v2 (legacy) : payload {"providers", "texts"}, réponse {provider: {"items": [...]}}.
        Le format est choisi selon l'URL (/v3/ → OpenAI) avec repli sur le parsing de l'autre.
        """
        import requests

        # Format OpenAI (payload {model, input}) pour EdenAI v3 (/v3/) ET pour
        # le service local TEI (/v1/embeddings). Sinon format v2 legacy.
        endpoint = self.config.endpoint or ""
        is_openai = "/v3/" in endpoint or "/v1/" in endpoint
        if is_openai:
            payload = {"model": self.config.provider, "input": texts}
        else:
            payload = {"providers": self.config.provider, "texts": texts}

        last_error = None
        for _ in range(self.config.max_retries):
            try:
                response = requests.post(
                    url=self.config.endpoint,
                    headers={
                        "Authorization": f"Bearer {self.config.secret_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.config.timeout,
                )
                # En cas d'erreur HTTP, EdenAI détaille la cause dans le corps :
                # on l'inclut dans l'exception pour diagnostiquer (ex. 400 = payload).
                if response.status_code >= 400:
                    import contextlib

                    body = ""
                    with contextlib.suppress(Exception):
                        body = response.text[:500]
                    raise RuntimeError(
                        f"HTTP {response.status_code} sur {self.config.endpoint} — {body}"
                    )
                data = response.json()
                vectors = self._parse_embeddings(data)
                if not vectors:
                    raise ValueError("Aucun embedding renvoyé")
                return vectors
            except Exception as ex:  # noqa: BLE001
                last_error = ex
        raise RuntimeError(f"Échec de l'embedding EdenAI: {last_error}")

    def _parse_embeddings(self, data: dict) -> list[list[float]]:
        """Extrait les vecteurs, quel que soit le format de réponse (v3 ou v2)."""
        if not isinstance(data, dict):
            return []

        # Format v3 / OpenAI : {"data": [{"embedding": [...], "index": N}], "usage": {...}}
        items = data.get("data")
        if (
            isinstance(items, list)
            and items
            and isinstance(items[0], dict)
            and "embedding" in items[0]
        ):
            ordered = sorted(items, key=lambda it: it.get("index", 0))
            usage = data.get("usage") or {}
            if isinstance(usage, dict):
                self.total_cost += float(usage.get("cost", 0) or data.get("cost", 0) or 0)
            return [it["embedding"] for it in ordered]

        # Format v2 legacy : {provider: {"items": [{"embedding": [...]}], "cost": ...}}
        provider_data = data.get(self.config.provider)
        if isinstance(provider_data, dict):
            v2_items = provider_data.get("items") or []
            self.total_cost += float(provider_data.get("cost", 0) or 0)
            return [it["embedding"] for it in v2_items if "embedding" in it]

        # Repli : chercher le premier dict de provider contenant des items.
        for value in data.values():
            if isinstance(value, dict) and isinstance(value.get("items"), list):
                self.total_cost += float(value.get("cost", 0) or 0)
                return [it["embedding"] for it in value["items"] if "embedding" in it]

        return []

    def embed_document(self, text: str) -> list[dict]:
        """Renvoie une liste de résultats {id, chunk_index, text, embedding}.

        Chaque embedding est normalisé L2. Vide si le texte est vide ou si aucun
        service d'embedding n'est configuré (EMBEDDING_LOCAL_URL non défini) — dans
        ce cas la cascade de classification saute simplement cet étage.
        """
        if not (self.config.endpoint or "").strip():
            return []
        cleaned = clean_text(text)
        if not cleaned:
            return []
        if self.config.max_chars and len(cleaned) > self.config.max_chars:
            cleaned = _smart_truncate(cleaned, self.config.max_chars)
        normalized = structural_normalize(cleaned)
        chunks = self._chunk_text(normalized)
        if not chunks:
            return []
        vectors = self._embed_batch(chunks)
        results = []
        for i, (chunk, vector) in enumerate(zip(chunks, vectors, strict=False)):
            results.append(
                {
                    "id": hashlib.sha256(chunk.encode()).hexdigest(),
                    "chunk_index": i,
                    "text": chunk,
                    "embedding": _normalize_vector(vector),
                }
            )
        return results

    def embedding(self, text: str) -> dict:
        """Embedding unique d'un texte (premier chunk) — pour le bootstrap."""
        results = self.embed_document(text)
        if not results:
            return {"embedding": []}
        return {"embedding": results[0]["embedding"]}
