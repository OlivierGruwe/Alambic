"""
alambic_core.repositories.base — repository générique.

Couche d'accès données. Isole les requêtes SQLAlchemy du code métier, qui
appelle des méthodes nommées (get/add/delete) plutôt que de manipuler la
Session directement. Mockable pour les tests unitaires, pointable sur un vrai
Postgres pour l'intégration.

Remplace get_by_id / save / create / update / delete de FclObject.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.base import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """CRUD générique typé sur un modèle.

    Sous-classer en fixant `model`, puis ajouter les requêtes métier :
        class DocumentRepository(BaseRepository[Document]):
            model = Document
            def by_transaction(self, tx_id): ...
    """

    model: type[ModelT]

    def __init__(self, session: Session):
        self.session = session

    def get(self, obj_id: str) -> ModelT | None:
        """Récupère par clé primaire. Remplace FclObject.get_by_id."""
        return self.session.get(self.model, obj_id)

    def list(self, limit: int = 100, offset: int = 0) -> list[ModelT]:
        stmt = select(self.model).limit(limit).offset(offset)
        return list(self.session.scalars(stmt).all())

    def add(self, obj: ModelT) -> ModelT:
        """Persiste un objet (sans commit — géré par session_scope)."""
        self.session.add(obj)
        self.session.flush()  # assigne l'id, valide les contraintes
        return obj

    def delete(self, obj: ModelT) -> None:
        self.session.delete(obj)
        self.session.flush()

    def count(self) -> int:
        from sqlalchemy import func

        return self.session.scalar(select(func.count()).select_from(self.model)) or 0
