"""
Couche d'accès données — façade au-dessus d'alambic_core.

Remplace l'ancienne version pseudo-SQL. L'orchestrateur (ingestion.py) appelle
toujours les mêmes méthodes (update_transaction, upsert_document, …), mais
derrière c'est désormais le vrai schéma SQLAlchemy d'alambic_core : modèles
typés, chiffrement Fernet des secrets, versioning optimiste, contraintes —
le tout déjà couvert par 34 tests.

C'est ici que vit l'ÉTAT du workflow (transactions/documents/messages), ce qui
redonne la visibilité que Step Functions offrait nativement : l'UI et le
SweepStuckTransactions lisent ces mêmes tables.

Gestion de session :
- Soit on injecte une Session SQLAlchemy existante (cas test / transaction
  partagée), via Repo(session=...).
- Soit on laisse Repo ouvrir/fermer sa propre session par opération, via
  session_scope() (cas worker Celery autonome). C'est le défaut.
"""

from __future__ import annotations

from contextlib import contextmanager

from alambic_core.db.session import session_scope
from alambic_core.models import Document, DocumentIndex
from alambic_core.repositories import (
    DocumentIndexRepository,
    DocumentRepository,
    MessageRepository,
    TransactionRepository,
)


class Repo:
    """Façade DB branchée sur alambic_core.

    Si `session` est fourni, toutes les opérations l'utilisent (et c'est à
    l'appelant de committer — utile pour grouper plusieurs écritures dans une
    seule transaction SQL). Sinon, chaque méthode ouvre sa propre session
    autonome via session_scope() (commit/rollback/close automatiques).
    """

    def __init__(self, session=None):
        self._session = session

    @contextmanager
    def _scope(self):
        """Fournit une session : celle injectée, ou une nouvelle autonome."""
        if self._session is not None:
            # Session injectée : on l'utilise sans la fermer (l'appelant gère).
            yield self._session
        else:
            # Session autonome : commit/rollback/close gérés par session_scope.
            with session_scope() as s:
                yield s

    # ── Transactions ─────────────────────────────────────────────────────────
    def update_transaction(self, transaction_id: str, status: str, process: str | None = None):
        """Remplace UpdateTransactionCreated/Extracted/Completed + FailTransaction.

        Le versioning optimiste d'alambic_core protège contre les écritures
        concurrentes (plusieurs documents mettant à jour la même transaction).
        """
        with self._scope() as s:
            tx = TransactionRepository(s).get(transaction_id)
            if tx is None:
                return
            tx.status = status
            if process is not None:
                tx.process = process

    # ── Documents ────────────────────────────────────────────────────────────
    def upsert_document(self, document_id: str, transaction_id: str, file: dict):
        """Remplace l'état CreateDocument (dynamodb:updateItem).

        upsert : crée le document s'il n'existe pas, sinon réinitialise son état.
        `file` est un dict {bucket, key} (l'objet S3/Garage source).
        """
        with self._scope() as s:
            repo = DocumentRepository(s)
            doc = repo.get(document_id)
            if doc is None:
                doc = Document(id=document_id, transaction_id=transaction_id)
                s.add(doc)
            doc.status = "CREATED"
            doc.process = "NEWDOC"
            doc.process_state = "STARTED"
            doc.transaction_id = transaction_id
            doc.bucket_name = file["bucket"]
            doc.object_key = file["key"]

    def mark_document_error(self, document_id: str):
        """Remplace MarkDocumentDispatchError dans le Map de dispatch."""
        with self._scope() as s:
            doc = DocumentRepository(s).get(document_id)
            if doc is not None:
                doc.status = "ERROR"

    # ── Index métadonnées (le Map "WriteMetadataIndexes") ───────────────────
    def put_metadata_index(self, document_id: str, name: str, value: str):
        """Remplace PutMetadataIndex. Le filtre (name/value non vides) est
        appliqué côté appelant, comme l'état Choice FilterEmptyMetadata.

        Idempotent : si un index metadata du même nom existe déjà pour ce
        document, on met à jour sa valeur plutôt que d'en créer un doublon.
        """
        with self._scope() as s:
            idx_repo = DocumentIndexRepository(s)
            existing = next(
                (i for i in idx_repo.metadata_of(document_id) if i.index_name == name),
                None,
            )
            if existing is not None:
                existing.index_value = value
            else:
                s.add(
                    DocumentIndex(
                        document_id=document_id,
                        index_type="metadata",
                        index_name=name,
                        index_value=value,
                    )
                )

    # ── Messages (journal d'erreurs) ─────────────────────────────────────────
    def add_message(self, parent_id: str, level: str, source: str, text: str):
        """Remplace FailTransactionMessage / MarkDocumentDispatchErrorMessage.

        parent_id désigne ici une transaction (chemin d'erreur du workflow
        d'ingestion). Pour un message rattaché à un document, voir
        add_document_message.
        """
        with self._scope() as s:
            MessageRepository(s).add_for_transaction(
                transaction_id=parent_id, text=text, level=level, source=source
            )

    def add_document_message(self, document_id: str, level: str, source: str, text: str):
        """Variante pour un message rattaché à un document (Map de dispatch)."""
        with self._scope() as s:
            MessageRepository(s).add_for_document(
                document_id=document_id, text=text, level=level, source=source
            )
