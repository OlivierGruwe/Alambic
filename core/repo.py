"""
Couche d'accès données — remplace les Task DynamoDB inlinées dans l'ASL.

Dans Step Functions, les états CreateDocument / UpdateTransaction* /
PutMetadataIndex / FailTransaction* étaient des appels DynamoDB directs
("arn:aws:states:::aws-sdk:dynamodb:..."). Ici on les regroupe dans un repo
PostgreSQL. C'est le point clé de la migration : l'ÉTAT du workflow vit dans
ces tables (transactions/documents/messages), pas dans le moteur Celery.

C'est ce qui te redonne la visibilité que Step Functions offrait nativement :
ton UI transactions.html et ton RetryPipelineFunction lisent ces tables.

NB : implémentation volontairement esquissée (pseudo-SQL) — le but de cette
démo est l'orchestration, pas le schéma complet. psycopg/SQLAlchemy en prod.
"""

from datetime import UTC, datetime


def _now():
    return datetime.now(UTC).isoformat()


class Repo:
    """Façade DB. En prod : pool psycopg ou session SQLAlchemy injectée."""

    def __init__(self, conn):
        self.conn = conn

    # ── Transactions ─────────────────────────────────────────────────────────
    def update_transaction(self, transaction_id, status, process=None):
        """Remplace UpdateTransactionCreated/Extracted/Completed + FailTransaction."""
        if process is not None:
            self.conn.execute(
                "UPDATE transactions SET status=%s, process=%s, "
                "last_modification_date=%s WHERE transaction_id=%s",
                (status, process, _now(), transaction_id),
            )
        else:
            self.conn.execute(
                "UPDATE transactions SET status=%s, last_modification_date=%s "
                "WHERE transaction_id=%s",
                (status, _now(), transaction_id),
            )

    # ── Documents ────────────────────────────────────────────────────────────
    def upsert_document(self, document_id, transaction_id, file):
        """Remplace l'état CreateDocument (dynamodb:updateItem)."""
        self.conn.execute(
            "INSERT INTO documents (document_id, status, process, process_state, "
            "transaction_id, bucket_name, object_key, version) "
            "VALUES (%s,'CREATED','NEWDOC','SUCCESS',%s,%s,%s,1) "
            "ON CONFLICT (document_id) DO UPDATE SET "
            "status='CREATED', process='NEWDOC', process_state='SUCCESS', "
            "transaction_id=EXCLUDED.transaction_id, "
            "bucket_name=EXCLUDED.bucket_name, object_key=EXCLUDED.object_key",
            (document_id, transaction_id, file["bucket"], file["key"]),
        )

    def mark_document_error(self, document_id):
        """Remplace MarkDocumentDispatchError dans le Map de dispatch."""
        self.conn.execute(
            "UPDATE documents SET status='ERROR', last_modification_date=%s WHERE document_id=%s",
            (_now(), document_id),
        )

    # ── Index métadonnées (le Map "WriteMetadataIndexes") ───────────────────
    def put_metadata_index(self, document_id, name, value):
        """Remplace PutMetadataIndex. Le filtre (name/value non vides) est
        appliqué côté appelant, comme l'état Choice FilterEmptyMetadata."""
        self.conn.execute(
            "INSERT INTO document_indexes (document_id, index_key, index_type, "
            "index_name, index_value) VALUES (%s,%s,'metadata',%s,%s) "
            "ON CONFLICT (document_id, index_key) DO UPDATE SET "
            "index_value=EXCLUDED.index_value",
            (document_id, f"metadata#{name}", name, value),
        )

    # ── Messages (journal d'erreurs) ─────────────────────────────────────────
    def add_message(self, parent_id, level, source, text):
        """Remplace FailTransactionMessage / MarkDocumentDispatchErrorMessage."""
        self.conn.execute(
            "INSERT INTO messages (parent_id, message_id, level, source, "
            "creation_date, text) VALUES (%s,%s,%s,%s,%s,%s)",
            (parent_id, f"{_now()}#{parent_id}", level, source, _now(), text),
        )
