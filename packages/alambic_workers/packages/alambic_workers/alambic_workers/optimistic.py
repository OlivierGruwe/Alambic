"""
optimistic_object — portage du context manager de flowerscan_lib (fcl_object.py).

Get-or-create d'un objet par son id, avec verrou optimiste au commit.

DIFFÉRENCE ASSUMÉE vs l'original flowerscan_lib :
l'original retentait 3 fois en interne sur conflit (boucle + backoff), car chaque
Lambda AWS était isolée et devait gérer ses conflits localement. Ici, le retry
vit au BON niveau : task_acks_late=True fait que Celery rejoue la tâche entière
si elle lève. On laisse donc StaleDataError remonter — un seul mécanisme de
reprise, pas de retry imbriqué dans un retry. Prérequis : tâches idempotentes
(upsert_document / put_metadata_index le sont déjà).

Infra : le verrou optimiste s'appuie sur le version_id_col SQLAlchemy (en place
et testé sur Document/Transaction), qui lève StaleDataError sur conflit — là où
flowerscan_lib s'appuyait sur le versioning conditionnel DynamoDB.

Usage :
    with optimistic_object(Document, doc_id, session=s) as doc:
        doc.status = "OK"
    # commit géré par l'appelant (session injectée) ou par session_scope (autonome)
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TypeVar

from alambic_core.db.base import Base
from alambic_core.db.session import session_scope
from sqlalchemy.orm import Session

ModelT = TypeVar("ModelT", bound=Base)


@contextmanager
def optimistic_object(
    model_cls: type[ModelT],
    obj_id: str,
    *,
    session: Session | None = None,
) -> Iterator[ModelT]:
    """Get-or-create + verrou optimiste.

    model_cls : la classe SQLAlchemy (Document, Transaction, …).
    obj_id    : l'identifiant (clé primaire). Crée l'objet s'il n'existe pas.
    session   : session injectée (l'appelant gère le commit) ; sinon une session
                autonome est ouverte via session_scope (commit/rollback auto).

    Sur conflit de version concurrent, StaleDataError remonte — la tâche Celery
    appelante sera rejouée intégralement (acks_late).

    Lève ValueError si obj_id est vide/None : les entités à id métier (Transaction
    → trx-…, Document → doc-…) DOIVENT recevoir leur id explicite. Sans ce garde-
    fou, le default=uuid_str des modèles générerait un id aléatoire à la place de
    l'id attendu, créant un objet fantôme introuvable (bug silencieux).
    """
    if not obj_id:
        raise ValueError(
            f"optimistic_object: id requis pour créer un {model_cls.__name__} "
            f"(reçu {obj_id!r}). L'id métier doit être fourni explicitement."
        )

    if session is not None:
        # Session injectée : l'appelant gère le commit (unité de travail partagée).
        obj = session.get(model_cls, obj_id)
        if obj is None:
            obj = model_cls(id=obj_id)
            session.add(obj)
        yield obj
        return

    # Session autonome : commit/rollback gérés par session_scope.
    with session_scope() as s:
        obj = s.get(model_cls, obj_id)
        if obj is None:
            obj = model_cls(id=obj_id)
            s.add(obj)
        yield obj
