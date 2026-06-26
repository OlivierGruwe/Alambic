"""Tests des composants de classification (cascade lexical→embedding→LLM)."""

from __future__ import annotations

import json
from unittest.mock import patch

from alambic_core.ai.category_registry import CategoryRegistry
from alambic_core.ai.document_classifier import DocumentClassifier
from alambic_core.ai.embedder import _normalize_vector, clean_text, structural_normalize

# ── Embedder : transformations de texte ─────────────────────────────────


def test_clean_text():
    assert clean_text("a\r\n\r\n\r\nb   c") == "a\n\nb c"


def test_structural_normalize_strips_page_markers():
    assert "[PAGE" not in structural_normalize("[PAGE 1] x [PAGE 2] y")


def test_normalize_vector_unit_norm():
    import numpy as np

    v = _normalize_vector([3.0, 4.0])
    assert abs(np.linalg.norm(np.array(v)) - 1.0) < 1e-6


# ── Registry ────────────────────────────────────────────────────────────


def test_registry_merges_categories():
    reg = CategoryRegistry(doctype_repository={"facture": {"description": "F", "fields": []}})
    assert "facture" in reg.categories


def test_registry_descriptions_for_prompt():
    reg = CategoryRegistry(
        doctype_repository={"facture": {"description": "Une facture", "fields": []}}
    )
    descs = reg.categories_with_description
    assert descs == [{"name": "facture", "description": "Une facture"}]


# ── Cascade : routage entre les trois étages ────────────────────────────


class _Lex:
    version = "t"

    def __init__(self, res=(None, 0, 0)):
        self.res = res

    def lexical_scoring(self, text):
        return self.res


class _Emb:
    def __init__(self, res=None):
        self.res = res if res is not None else [{"embedding": [1, 0, 0]}]

    def embed_document(self, text):
        return self.res


class _VS:
    prod_version = "t"
    categories = ["facture", "contrat"]

    def __init__(self, res=("facture", 0.95, 0.5)):
        self.res = res

    def score(self, vec):
        return self.res


class _LLM:
    def __init__(self):
        self.registry = None
        self.called = False

    def set_registry(self, r):
        self.registry = r

    def classify(self, text):
        self.called = True
        return {"type": "facture_llm", "confidence": 0.8, "description": "d", "fields": []}, 0.02


_DOCTYPES = {"facture": {"description": "F", "fields": [{"field_name": "montant"}]}}


def _clf(lex, emb, llm, vs):
    reg = CategoryRegistry(doctype_repository=_DOCTYPES)
    return DocumentClassifier(
        lexical_engine=lex, embedder=emb, llm=llm, vector_store=vs, category_registry=reg
    )


def test_cascade_lexical_wins():
    clf = _clf(_Lex(("facture", 0.9, 0.5)), _Emb(), _LLM(), _VS())
    r = clf.classify_document("x")
    assert r.type == "facture" and "lexical" in r.source


def test_cascade_embedding_wins_no_llm():
    llm = _LLM()
    clf = _clf(_Lex(), _Emb(), llm, _VS(("facture", 0.95, 0.5)))
    r = clf.classify_document("x")
    assert "embedding" in r.source
    assert not llm.called
    assert r.fields == [{"field_name": "montant"}]  # enrichi


def test_cascade_llm_fallback():
    llm = _LLM()
    clf = _clf(_Lex(), _Emb(), llm, _VS(("facture", 0.4, 0.05)))  # embedding trop faible
    r = clf.classify_document("x")
    assert llm.called and "llm" in r.source and r.cost == 0.02


def test_cascade_empty_embedding_goes_llm():
    llm = _LLM()
    clf = _clf(_Lex(), _Emb(res=[]), llm, _VS())
    clf.classify_document("x")
    assert llm.called


# ── Vector store : scoring ──────────────────────────────────────────────


def test_vector_store_scoring():
    import threading

    import numpy as np

    from alambic_core.ai.vector_store import CategoryVectorStore, _normalize_np

    vs = CategoryVectorStore.__new__(CategoryVectorStore)
    vs.reload_interval = 300
    vs._last_check = 9e18
    vs._lock = threading.Lock()
    vs.prod_matrix = np.array(
        [_normalize_np([1, 0, 0]), _normalize_np([0, 1, 0])], dtype=np.float32
    )
    vs.label_index = ["facture", "contrat"]
    vs.prod_version = "t"

    label, score, delta = vs.score([0.9, 0.1, 0.0])
    assert label == "facture" and score > 0.9 and delta > 0


# ── Compaction incrémentale ─────────────────────────────────────────────


def test_vector_compaction_roundtrip():
    from alambic_core.services import vector_compactor as vc

    fake = {}

    def put(b, k, c):
        fake[k] = c

    def get(b, k):
        if k not in fake:
            raise KeyError(k)
        return fake[k]

    def lst(b, prefix=""):
        return [{"Key": k} for k in fake if k.startswith(prefix)]

    with (
        patch.object(vc.storage, "put_bytes", put),
        patch.object(vc.storage, "get_bytes", get),
        patch.object(vc.storage, "list_objects", lst),
        patch.object(vc.storage, "work_bucket", lambda: "work"),
    ):
        vc.append_vector_log(
            bucket="work", doctype="facture", embedding=[1.0, 0.0, 0.0], source_text="a"
        )
        vc.append_vector_log(
            bucket="work", doctype="contrat", embedding=[0.0, 1.0, 0.0], source_text="b"
        )
        result = vc.compact("work")
        assert result["status"] == "ok" and result["logs"] == 2

        latest = json.loads(fake["__vectors_prod__/latest.json"])
        model = json.loads(fake[latest["path"]])
        assert set(model["metadata"]["classes"]) == {"facture", "contrat"}

        # Curseur : pas de retraitement.
        assert vc.compact("work")["status"] == "no_new_logs"


# ── Embedder : compatibilité formats v2 / v3 + dégradation ──────────────


def test_embedder_parses_v3_openai_format():
    from alambic_core.ai.embedder import EdenAIEmbedder, EmbeddingConfig

    emb = EdenAIEmbedder(
        EmbeddingConfig(
            secret_key="k",
            endpoint="https://api.edenai.run/v3/llm/embeddings",
            provider="openai/text-embedding-3-small",
        )
    )
    data = {
        "data": [{"embedding": [0.1, 0.2], "index": 0}, {"embedding": [0.4, 0.5], "index": 1}],
        "usage": {"cost": 0.0001},
    }
    assert emb._parse_embeddings(data) == [[0.1, 0.2], [0.4, 0.5]]
    assert emb.total_cost == 0.0001


def test_embedder_v3_reorders_by_index():
    from alambic_core.ai.embedder import EdenAIEmbedder, EmbeddingConfig

    emb = EdenAIEmbedder(EmbeddingConfig(secret_key="k", endpoint="https://x/v3/y", provider="p"))
    data = {"data": [{"embedding": [9], "index": 1}, {"embedding": [8], "index": 0}]}
    assert emb._parse_embeddings(data) == [[8], [9]]


def test_embedder_parses_v2_legacy_format():
    from alambic_core.ai.embedder import EdenAIEmbedder, EmbeddingConfig

    emb = EdenAIEmbedder(
        EmbeddingConfig(
            secret_key="k",
            endpoint="https://api.edenai.run/v2/text/embeddings",
            provider="openai/text-embedding-3-small",
        )
    )
    data = {
        "openai/text-embedding-3-small": {
            "items": [{"embedding": [1, 2]}, {"embedding": [3, 4]}],
            "cost": 0.0002,
        }
    }
    assert emb._parse_embeddings(data) == [[1, 2], [3, 4]]


def test_classifier_degrades_when_embedding_fails():
    """Un échec d'embedding (404, provider en panne) bascule sur le LLM."""
    from alambic_core.ai.category_registry import CategoryRegistry
    from alambic_core.ai.document_classifier import DocumentClassifier

    class _LexFail:
        version = "t"

        def lexical_scoring(self, t):
            return (None, 0, 0)

    class _EmbFail:
        def embed_document(self, t):
            raise RuntimeError("404 Not Found")

    class _VS:
        prod_version = "t"
        categories = ["facture"]

        def score(self, v):
            return ("facture", 0.9, 0.5)

    class _LLM:
        def __init__(self):
            self.registry = None
            self.called = False

        def set_registry(self, r):
            self.registry = r

        def classify(self, t):
            self.called = True
            return {"type": "facture", "confidence": 0.8, "description": "", "fields": []}, 0.02

    reg = CategoryRegistry(doctype_repository={"facture": {"description": "F", "fields": []}})
    llm = _LLM()
    clf = DocumentClassifier(
        lexical_engine=_LexFail(),
        embedder=_EmbFail(),
        llm=llm,
        vector_store=_VS(),
        category_registry=reg,
    )
    result = clf.classify_document("texte")
    assert llm.called
    assert "llm" in result.source
