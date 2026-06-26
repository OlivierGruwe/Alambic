"""
Tâches d'ingestion — le "travail" réel, piloté par orchestration/ingestion.py.

Correspondance avec les Lambdas FlowerScan :
    CreateTransaction   -> create_transaction()
    ExtractFiles        -> extract_files()    (ExtractFilesArn)
    CreateDocuments     -> create_documents() (CreateDocumentsArn)

Ces fonctions sont appelées DIRECTEMENT par l'orchestrateur (pas via .delay()),
car elles font partie d'une même unité de travail séquentielle. Elles restent
néanmoins déclarables comme tâches Celery si tu veux les exposer/réessayer
individuellement (décorateur commenté en bas).

État d'avancement :
  - create_transaction : branché sur alambic_core (crée une vraie Transaction).
  - extract_files / create_documents : SQUELETTE. La logique métier (lecture du
    bucket Garage, split, détection multi-docs) dépend de tes Lambdas d'origine —
    à compléter. Les TODO marquent précisément où.
"""

from __future__ import annotations

import os
import uuid

from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentProcess, DocumentStatus
from alambic_core.models import Document, Message, Transaction
from alambic_core.pipeline import step
from alambic_core.pipeline.processors import build_engine
from alambic_core.repositories import TransactionRepository

from alambic_workers import storage


def _new_transaction_id() -> str:
    """Génère un identifiant de transaction au format historique 'trx-...'."""
    return f"trx-{uuid.uuid4().hex[:16]}"


def create_transaction(payload: dict) -> dict:
    """État CreateTransaction — crée la transaction ET le document racine.

    Entrée  : payload de la machine d'état (cid, configId, accountId, documents…).
    Sortie  : payload enrichi de payload["transaction"]["transactionId"].

    La transaction démarre en statut WORKING / process NEWDOC. Le document racine
    (le fichier déposé) est créé dans la même unité de travail : c'est le premier
    document de la transaction et la racine de l'arbre de filiation (parent_id
    null). Ses enfants éventuels (extraction eml/zip) pointeront vers lui.
    """
    tx_meta = payload.get("transaction", {})
    tx_id = tx_meta.get("transactionId") or _new_transaction_id()
    transaction_key = tx_meta.get("transaction_key", "")

    # origin : présent dans les datas du payload ({name, value}).
    origin = ""
    for item in payload.get("datas", []):
        if item.get("name") == "origin":
            origin = item.get("value", "")
            break

    # Document racine : le premier (et unique à ce stade) document du payload.
    documents = payload.get("documents", [])
    root = documents[0] if documents else {}
    root_doc_id = root.get("documentId")
    root_file = root.get("file", {})

    with session_scope() as s:
        repo = TransactionRepository(s)
        tx = repo.get(tx_id)
        if tx is None:
            tx = Transaction(
                id=tx_id,
                transaction_key=transaction_key,
                status="WORKING",
                process="NEWDOC",
                origin=origin,
                account_id=payload.get("accountId"),
                config_id=payload.get("configId"),
                nb_docs=len(documents),
            )
            s.add(tx)
        elif transaction_key and not tx.transaction_key:
            # Idempotence du workflow (replay Celery) : on complète la clé sans
            # écraser le reste de l'état.
            tx.transaction_key = transaction_key

        # Document racine — upsert idempotent (replay-safe).
        if root_doc_id is not None:
            doc = s.get(Document, root_doc_id)
            if doc is None:
                s.add(
                    Document(
                        id=root_doc_id,
                        transaction_id=tx_id,
                        parent_id=None,  # racine de l'arbre
                        status=DocumentStatus.CREATED.value,
                        process=DocumentProcess.NEWDOC.value,
                        bucket_name=root_file.get("bucket", ""),
                        object_key=root_file.get("key", ""),
                    )
                )

    payload.setdefault("transaction", {})["transactionId"] = tx_id
    return payload


def extract_files(payload: dict) -> dict:
    """État ExtractFiles — extrait le document racine en N documents enfants.

    Pour un zip/eml : décompose le fichier source en fichiers individuels,
    crée un Document enfant par fichier extrait (parent_id = document racine),
    déprécie le document racine (DEPRECATED, remplacé par ses enfants), et écarte
    les fichiers inexploitables (DISCARDED + message qui remonte à la transaction).

    Pour un fichier simple (pdf, image…) : un seul fichier extrait = le document
    racine reste l'unique document actif (pas de filiation, pas de dépréciation).

    Encadré par `step(FILEEXTRACTOR)` : MAJ DB, durée, rejouabilité.
    """
    import tempfile

    tx_id = payload["transaction"]["transactionId"]
    root = payload.get("documents", [{}])[0]
    root_doc_id = root.get("documentId")
    root_file = root.get("file", {})
    src_bucket = root_file.get("bucket", "")
    src_key = root_file.get("key", "")

    with step(tx_id, "FILEEXTRACTOR", document_id=root_doc_id) as st:
        if st.skipped:
            return payload

        work_dir = tempfile.mkdtemp(prefix="alambic_extract_")
        out_dir = tempfile.mkdtemp(prefix="alambic_out_")

        # 1. Télécharger le fichier source depuis Garage.
        local_src = os.path.join(work_dir, os.path.basename(src_key) or "source")
        storage.download_to(src_bucket, src_key, local_src)

        # 2. Extraire via le moteur (zip/eml/défaut), récursif et borné.
        engine = build_engine()
        result = engine.process(local_src, out_dir)

        # 3. Décider du périmètre : un seul fichier ok ET pas d'écarté → mono-doc
        #    (le document racine reste l'unique document actif).
        ok_files = result.ok_files
        prefix = os.path.dirname(src_key)
        new_documents: list[dict] = []

        single = len(ok_files) == 1 and not result.error_files and not result.unsupported_files

        if single:
            # Mono-document : on ré-uploade le fichier extrait sous une clé propre
            # et on garde le document racine comme document actif.
            fe = ok_files[0]
            ext = os.path.splitext(fe.filename)[1]
            key = f"{prefix}/{root_doc_id}{ext}"
            storage.put_object(src_bucket, key, fe.path)
            _update_root_document(root_doc_id, src_bucket, key, fe.type)
            new_documents = [
                {"documentId": root_doc_id, "file": {"bucket": src_bucket, "key": key}}
            ]
        else:
            # Multi-document : un Document enfant par fichier ok, parent déprécié.
            new_documents = _create_children(tx_id, root_doc_id, src_bucket, prefix, result)
            _deprecate_root(root_doc_id)
            _discard_unsupported(tx_id, root_doc_id, result)

        payload["documents"] = new_documents

    return payload


def _update_root_document(doc_id: str, bucket: str, key: str, file_type: str) -> None:
    """Cas mono-doc : le document racine pointe vers le fichier extrait."""
    with session_scope() as s:
        doc = s.get(Document, doc_id)
        if doc is not None:
            doc.bucket_name = bucket
            doc.object_key = key
            doc.status = DocumentStatus.CREATED.value


def _create_children(tx_id: str, parent_id: str, bucket: str, prefix: str, result) -> list[dict]:
    """Crée un Document enfant par fichier ok extrait (parent_id = racine)."""
    children: list[dict] = []
    with session_scope() as s:
        for i, fe in enumerate(result.ok_files, start=1):
            child_id = f"{parent_id}_{str(i).zfill(5)}"
            ext = os.path.splitext(fe.filename)[1]
            key = f"{prefix}/{child_id}{ext}"
            # Upload du fichier extrait vers Garage.
            storage.put_object(bucket, key, fe.path)
            doc = s.get(Document, child_id)
            if doc is None:
                doc = Document(
                    id=child_id,
                    transaction_id=tx_id,
                    parent_id=parent_id,
                    status=DocumentStatus.CREATED.value,
                    process=DocumentProcess.NEWDOC.value,
                    bucket_name=bucket,
                    object_key=key,
                )
                s.add(doc)
            children.append({"documentId": child_id, "file": {"bucket": bucket, "key": key}})
    return children


def _deprecate_root(root_doc_id: str) -> None:
    """Déprécie le document racine (remplacé par ses enfants). Sans raison."""
    with session_scope() as s:
        doc = s.get(Document, root_doc_id)
        if doc is not None:
            doc.status = DocumentStatus.DEPRECATED.value


def _discard_unsupported(tx_id: str, parent_id: str, result) -> None:
    """Écarte les fichiers inexploitables (DISCARDED) et fait remonter l'info."""
    discarded = result.unsupported_files + result.error_files
    if not discarded:
        return
    with session_scope() as s:
        tx = s.get(Transaction, tx_id)
        for i, fe in enumerate(discarded, start=1):
            reason = fe.message or fe.error_code or "fichier inexploitable"
            child_id = f"{parent_id}_discarded_{str(i).zfill(5)}"
            s.add(
                Document(
                    id=child_id,
                    transaction_id=tx_id,
                    parent_id=parent_id,
                    status=DocumentStatus.DISCARDED.value,
                    discard_reason=reason,
                    bucket_name="",
                    object_key="",
                )
            )
            # L'info remonte à la transaction (message visible dans le suivi).
            s.add(
                Message(
                    transaction_id=tx_id,
                    level="WARNING",
                    source="extract_files",
                    text=f"Document écarté ({fe.filename}) : {reason}",
                )
            )
        if tx is not None:
            tx.nb_discarded = (tx.nb_discarded or 0) + len(discarded)


def create_documents(payload: dict) -> dict:
    """État CreateDocuments (CreateDocumentsArn) — SQUELETTE.

    Rôle attendu : pour chaque fichier extrait, créer une entrée document
    (statut CREATED) et préparer la liste payload["documents"] que le dispatch
    parcourra ensuite.

    TODO :
      1. Pour chaque fichier de payload["extracted_files"], créer un Document
         via DocumentRepository (ou le Repo façade) rattaché à la transaction.
      2. Construire payload["documents"] = [{documentId, file}, ...].

    Pour l'instant : reprend le document unique déjà préparé par l'orchestrateur.
    """
    # TODO: créer un Document par fichier extrait (multi-docs). Mono-doc en attendant.
    if "documents" not in payload and "document" in payload:
        payload["documents"] = [payload["document"]]
    return payload


# Si tu veux exposer ces fonctions comme tâches Celery individuelles (retry,
# monitoring séparé), décommente et enveloppe — l'orchestrateur peut alors
# choisir entre l'appel direct (séquentiel) et .delay() (distribué) :
#
# from alambic_workers.celery_app import app
#
# @app.task(name="alambic_workers.tasks.ingestion.extract_files", bind=True,
#           autoretry_for=(Exception,), max_retries=3, retry_backoff=True)
# def extract_files_task(self, payload: dict) -> dict:
#     return extract_files(payload)
