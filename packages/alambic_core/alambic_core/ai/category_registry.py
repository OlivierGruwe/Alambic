"""alambic_core.ai.category_registry — registre unifié des catégories.

Porté de FlowerScan (fcl_category_registry). Fait l'union des catégories
connues depuis trois sources : les doctypes (source métier, avec descriptions),
le vector store (catégories ayant des centroïdes) et le lexical engine
(catégories ayant des statistiques). Fournit la liste au LLM classifier.
"""

from __future__ import annotations

import contextlib


class CategoryRegistry:
    """Union des catégories métier (doctypes) et apprises (vecteurs, lexical)."""

    def __init__(
        self, *, vector_store=None, lexical_engine=None, doctype_repository: dict | None = None
    ):
        self.vector_store = vector_store
        self.lexical_engine = lexical_engine
        # doctype_repository : {doctype_name: doctype_obj_ou_dict}
        self.doctype_repository = doctype_repository or {}

    def get_doctype(self, name: str):
        return self.doctype_repository.get(name)

    @property
    def categories(self) -> list[str]:
        """Union triée de toutes les catégories connues."""
        categories: set[str] = set()
        if self.doctype_repository:
            categories.update(self.doctype_repository.keys())
        if self.vector_store is not None:
            with contextlib.suppress(Exception):
                categories.update(self.vector_store.categories)
        if self.lexical_engine is not None:
            with contextlib.suppress(Exception):
                categories.update(self.lexical_engine.categories)
        return sorted(categories)

    @property
    def categories_with_description(self) -> list[dict]:
        """Catégories enrichies de leur description (pour le prompt LLM).

        Seuls les doctypes portent une description ; les catégories issues des
        modèles appris ont une description vide (le LLM se débrouille avec le nom).
        """
        result = []
        seen: set[str] = set()
        for name, dt in self.doctype_repository.items():
            if name in seen:
                continue
            seen.add(name)
            desc = ""
            if isinstance(dt, dict):
                desc = dt.get("description", "") or ""
            else:
                desc = getattr(dt, "description", "") or ""
            result.append({"name": name, "description": desc.strip()})
        for name in self.categories:
            if name not in seen:
                result.append({"name": name, "description": ""})
        return sorted(result, key=lambda x: x["name"])

    @property
    def version(self) -> str:
        if self.vector_store is not None:
            return getattr(self.vector_store, "prod_version", "unknown")
        return "unknown"
