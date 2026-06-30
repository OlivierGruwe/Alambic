"""
═══════════════════════════════════════════════════════════════════════════════
ORCHESTRATEUR INGESTION — remplace IngestionStateMachine (01_Ingestion.asl.json)
═══════════════════════════════════════════════════════════════════════════════

Traduction état-par-état de l'ASL :

    ASL (Step Functions)                  Ici (Celery)
    ────────────────────────────────────  ──────────────────────────────────────
    CheckResumeMode (Choice)              if payload.get("resume_from")==...
    CreateTransaction (Task)              create_transaction()
    PrepareSplit / PrepareDocumentId      _prepare_document() — pure Python
    CreateDocument (dynamodb:updateItem)  repo.upsert_document()
    WriteMetadataIndexes (Map+Choice)     boucle filtrée -> repo.put_metadata_index
    UpdateTransaction* (dynamodb)         repo.update_transaction()
    ExtractFiles / CreateDocuments (Task) appels de fonctions séquentiels
    DispatchProcessing (Map, sync:2)      group() de run_processing
    FailTransaction* / FailTransactionEarly  bloc except -> repo + add_message

POURQUOI un orchestrateur explicite (vs une simple chain Celery) :
l'ASL intercale des écritures d'état DB entre chaque étape (WORKING/DOC_CREATED,
WORKING/DOC_EXTRACTED, COMPLETED) et a deux chemins d'erreur distincts
(avant/après que la transaction existe). Un orchestrateur explicite reproduit
ça lisiblement. C'est lui qui porte la "durabilité d'état" que Step Functions
donnait gratuitement — l'état vit dans les tables transactions/documents/messages
d'alambic_core, pas dans le moteur Celery.

CHOIX DE CHAÎNAGE (le plus solide pour ce cas) :
les étapes internes (create_transaction, extract_files, create_documents) sont
appelées comme de SIMPLES FONCTIONS, pas via .delay()/.apply() ni chain. Raison :
elles font partie d'une même unité de travail séquentielle pilotée par cet
orchestrateur, qui écrit l'état entre chacune. Pas d'attente inter-worker (anti-
pattern Celery), pas de dispersion de la logique d'état. Le mécanisme distribué
de Celery (group) est réservé à ce qui doit VRAIMENT être parallèle : le
dispatch d'un Processing par document.

⚠️ Limite assumée : si CE process meurt entre deux étapes, pas de replay
automatique. La reprise se fait via resume_from + SweepStuckTransactions qui
relit la table transactions. C'est le design retenu — faible surcoût.
"""

from __future__ import annotations

from celery import group

from alambic_workers.celery_app import app
from alambic_workers.repo import Repo
from alambic_workers.tasks.ingestion import (
    create_documents,
    create_transaction,
    extract_files,
)

SOURCE = "01_Ingestion"


# ── Intrinsics ASL réimplémentées en Python pur (états Pass) ─────────────────
def _prepare_document(payload: dict) -> dict:
    """États PrepareSplit + PrepareDocumentId.

    ASL : States.StringSplit($.transaction.transactionId, 'trx-') puis
    States.Format('doc-{}', ...). Trivial en Python.
    """
    tx_id = payload["transaction"]["transactionId"]
    split_id = tx_id.split("trx-")  # States.StringSplit
    suffix = split_id[0] if split_id[0] else (split_id[1] if len(split_id) > 1 else tx_id)
    payload["transactionId"] = tx_id
    payload["document"] = {
        "file": payload["documents"][0]["file"],
        "documentId": f"doc-{suffix}",  # States.Format('doc-{}', ...)
    }
    return payload


def _write_metadata_indexes(repo: Repo, payload: dict) -> None:
    """État Map WriteMetadataIndexes + Choice FilterEmptyMetadata.

    Le filtre (name/value non vides) est reproduit tel quel = l'état Choice
    FilterEmptyMetadata. put_metadata_index est idempotent côté repo.
    """
    document_id = payload["document"]["documentId"]
    for item in payload.get("datas", []):
        name, value = item.get("name"), item.get("value")
        if name and value:  # = Choice FilterEmptyMetadata
            repo.put_metadata_index(document_id, name, value)
        # else: équivalent SkipMetadataIndex (no-op)


# ── Le workflow principal ────────────────────────────────────────────────────
@app.task(name="alambic_workers.ingestion.run", bind=True, acks_late=True)
def run_ingestion(self, payload: dict) -> dict:
    """Point d'entrée — équivalent d'une exécution de l'IngestionStateMachine.

    Déclenché depuis l'événement S3/MinIO (Garage). `payload` = l'input de la SM.
    Le Repo ouvre ses propres sessions autonomes (session_scope), donc le worker
    doit avoir appelé init_core() au démarrage (fait par celery_app).
    """
    repo = Repo()

    # ── CheckResumeMode (Choice) ─────────────────────────────────────────────
    if payload.get("resume_from") == "DISPATCH":
        # ResumeDispatch : documents/transactionId déjà dans l'input
        return _dispatch_processing(repo, payload)

    # ── CreateTransaction + Catch -> FailTransactionEarly ────────────────────
    try:
        payload = create_transaction(payload)
    except Exception as e:
        # FailTransactionEarly : transactionId pas encore à la racine
        tx_id = payload.get("transaction", {}).get("transactionId")
        if tx_id:
            repo.update_transaction(tx_id, status="ERROR")
            repo.add_message(tx_id, "ERROR", SOURCE, f"Error: {e}")
        raise

    # À partir d'ici transactionId existe -> chemin d'erreur "normal"
    try:
        # ── PrepareSplit + PrepareDocumentId (Pass) ──────────────────────────
        payload = _prepare_document(payload)
        tx_id = payload["transactionId"]

        # ── CreateDocument (dynamodb:updateItem) ─────────────────────────────
        repo.upsert_document(payload["document"]["documentId"], tx_id, payload["document"]["file"])

        # ── WriteMetadataIndexes (Map + Choice filter) ───────────────────────
        _write_metadata_indexes(repo, payload)

        # ── UpdateTransactionCreated : WORKING / DOC_CREATED ─────────────────
        repo.update_transaction(tx_id, status="WORKING", process="DOC_CREATED")

        # ── ExtractFiles -> CreateDocuments (séquentiel, même unité de travail)
        payload = extract_files(payload)
        payload = create_documents(payload)

        # ── UpdateTransactionExtracted : WORKING / DOC_EXTRACTED ─────────────
        repo.update_transaction(tx_id, status="WORKING", process="DOC_EXTRACTED")

        # ── DispatchProcessing (Map parallèle, un Processing par document) ───
        _dispatch_processing(repo, payload)

        # ── UpdateTransactionCompleted : COMPLETED / DISPATCH_DONE ───────────
        repo.update_transaction(tx_id, status="COMPLETED", process="DISPATCH_DONE")
        return {"status": "COMPLETED", "transactionId": tx_id}

    except Exception as e:
        # ── FailTransaction + FailTransactionMessage -> WorkflowFailed ───────
        tx_id = payload.get("transactionId")
        if tx_id:
            repo.update_transaction(tx_id, status="ERROR")
            repo.add_message(tx_id, "ERROR", SOURCE, f"Error: {e}")
        raise


def _dispatch_processing(repo: Repo, payload: dict):
    """État DispatchProcessing (Type: Map, MaxConcurrency 10).

    ASL lançait un ProcessingStateMachine EXPRESS par document, en parallèle.
    En Celery : un group() de run_processing, une par document. Le parallélisme
    réel vient du nombre de workers sur la queue (--concurrency), pas d'un
    MaxConcurrency déclaratif.

    C'est ICI qu'on utilise le mécanisme distribué de Celery (et nulle part
    ailleurs dans ce workflow) : le dispatch est la seule étape réellement
    parallèle.
    """
    # Import tardif pour éviter le cycle (processing importera des trucs d'ici)
    from alambic_workers.orchestration.processing import run_processing

    documents = payload.get("documents", [])
    job = group(
        run_processing.s(
            {
                "cid": payload.get("cid"),
                "transactionId": payload["transactionId"],
                "configId": payload.get("configId"),
                "accountId": payload.get("accountId"),
                "document": doc,
                "process": "PROCESSING",
            }
        )
        for doc in documents
    )
    return job.apply_async()
