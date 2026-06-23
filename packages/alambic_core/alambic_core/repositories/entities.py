"""
alambic_core.repositories.entities — repositories métier.

Remplacent les FclXxxRepository de flowerscan_lib. Chaque requête fréquente du
pipeline devient une méthode nommée et testable.
"""

from __future__ import annotations

from sqlalchemy import select

from ..domain.enums import IndexType
from ..models import (
    Account,
    Config,
    Cost,
    Doctype,
    Document,
    DocumentIndex,
    Message,
    Transaction,
)
from .base import BaseRepository


class AccountRepository(BaseRepository[Account]):
    model = Account


class ConfigRepository(BaseRepository[Config]):
    model = Config

    def by_account(self, account_id: str) -> list[Config]:
        return list(
            self.session.scalars(select(Config).where(Config.account_id == account_id)).all()
        )


class DoctypeRepository(BaseRepository[Doctype]):
    model = Doctype

    def public_and_account(self, account_id: str) -> list[Doctype]:
        """Doctypes publics + ceux du compte (logique d'accès de FclDoctype)."""
        return list(
            self.session.scalars(
                select(Doctype).where(
                    (Doctype.is_public.is_(True)) | (Doctype.account_id == account_id)
                )
            ).all()
        )


class TransactionRepository(BaseRepository[Transaction]):
    model = Transaction

    def by_status(self, status: str) -> list[Transaction]:
        return list(
            self.session.scalars(select(Transaction).where(Transaction.status == status)).all()
        )

    def by_transaction_key(self, transaction_key: str) -> Transaction | None:
        """Retrouve une transaction par sa clé déterministe (hash bucket+clé S3).

        Sert à l'idempotence du déclencheur d'ingestion : si une transaction
        existe déjà pour cette clé, on ne relance pas le workflow.
        """
        return self.session.scalars(
            select(Transaction).where(Transaction.transaction_key == transaction_key)
        ).first()

    def by_account(self, account_id: str) -> list[Transaction]:
        return list(
            self.session.scalars(
                select(Transaction).where(Transaction.account_id == account_id)
            ).all()
        )


class DocumentRepository(BaseRepository[Document]):
    model = Document

    def by_transaction(self, transaction_id: str) -> list[Document]:
        return list(
            self.session.scalars(
                select(Document).where(Document.transaction_id == transaction_id)
            ).all()
        )

    def by_status(self, status: str) -> list[Document]:
        return list(self.session.scalars(select(Document).where(Document.status == status)).all())


class DocumentIndexRepository(BaseRepository[DocumentIndex]):
    """Remplace FclDocumentIndexRepository. query_parent + filtres par type."""

    model = DocumentIndex

    def by_document(self, document_id: str) -> list[DocumentIndex]:
        """Équivalent de query_parent(parent_id)."""
        return list(
            self.session.scalars(
                select(DocumentIndex).where(DocumentIndex.document_id == document_id)
            ).all()
        )

    def metadata_of(self, document_id: str) -> list[DocumentIndex]:
        return self._by_type(document_id, IndexType.METADATA.value)

    def extracted_of(self, document_id: str) -> list[DocumentIndex]:
        return self._by_type(document_id, IndexType.EXTRACTED.value)

    def _by_type(self, document_id: str, index_type: str) -> list[DocumentIndex]:
        return list(
            self.session.scalars(
                select(DocumentIndex).where(
                    DocumentIndex.document_id == document_id,
                    DocumentIndex.index_type == index_type,
                )
            ).all()
        )


class MessageRepository(BaseRepository[Message]):
    """Remplace FclMessageRepository. Messages par parent (tx ou doc)."""

    model = Message

    def for_transaction(self, transaction_id: str) -> list[Message]:
        return list(
            self.session.scalars(
                select(Message)
                .where(Message.transaction_id == transaction_id)
                .order_by(Message.created_at)
            ).all()
        )

    def for_document(self, document_id: str) -> list[Message]:
        return list(
            self.session.scalars(
                select(Message)
                .where(Message.document_id == document_id)
                .order_by(Message.created_at)
            ).all()
        )

    def add_for_transaction(
        self, transaction_id: str, text: str, level: str = "INFO", source: str = ""
    ) -> Message:
        msg = Message(transaction_id=transaction_id, text=text, level=level, source=source)
        return self.add(msg)

    def add_for_document(
        self, document_id: str, text: str, level: str = "INFO", source: str = ""
    ) -> Message:
        msg = Message(document_id=document_id, text=text, level=level, source=source)
        return self.add(msg)


class CostRepository(BaseRepository[Cost]):
    model = Cost

    def by_transaction(self, transaction_id: str) -> list[Cost]:
        return list(
            self.session.scalars(select(Cost).where(Cost.transaction_id == transaction_id)).all()
        )
