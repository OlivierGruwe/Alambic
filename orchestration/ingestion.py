"""
═══════════════════════════════════════════════════════════════════════════════
ORCHESTRATEUR INGESTION — remplace IngestionStateMachine (01_Ingestion.asl.json)
═══════════════════════════════════════════════════════════════════════════════

Traduction état-par-état de l'ASL. La table de correspondance :

    ASL (Step Functions)                  Ici (Celery)
    ────────────────────────────────────  ──────────────────────────────────────
    CheckResumeMode (Choice)              if payload.get("resume_from")==...
    CreateTransaction (Task)              create_transaction.delay()
    PrepareSplit / PrepareDocumentId      _prepare_document() — pure Python
      (Pass + intrinsics States.*)          (States.StringSplit/Format = f-strings)
    CreateDocument (dynamodb:updateItem)  repo.upsert_document()
    WriteMetadataIndexes (Map+Choice)     group() de put_metadata_index, filtré
    UpdateTransaction* (dynamodb)         repo.update_transaction()
    ExtractFiles / CreateDocuments (Task) chain de tasks Celery
    DispatchProcessing (Map, sync:2)      chord/group de start_processing
    FailTransaction* / FailTransactionEarly  bloc except -> repo + add_message

POURQUOI ce fichier existe (vs juste une chain Celery) :
ton ASL n'est pas une simple séquence — il intercale des écritures d'état DB
entre chaque task (WORKING/DOC_CREATED, WORKING/DOC_EXTRACTED, COMPLETED) et a
deux chemins d'erreur distincts (avant/après que transactionId existe). Un
orchestrateur explicite reproduit ça lisiblement. C'est lui qui porte la
"durabilité d'état" que Step Functions te donnait gratuitement.

⚠️ Limite assumée (cf. notre échange) : si CE process meurt entre deux étapes,
il n'y a pas de replay automatique du workflow comme Step Functions. La reprise
se fait via resume_from + le SweepStuckTransactions qui relit la table
transactions. C'est exactement ton design actuel — d'où le faible surcoût.
"""

from celery import chain, group, chord
from core.celery_app import app
from core.repo import Repo
from tasks.ingestion import create_transaction, extract_files, create_documents

SOURCE = "01_Ingestion"


# ── Intrinsics ASL réimplémentées en Python pur (états Pass) ─────────────────
def _prepare_document(payload: dict) -> dict:
    """États PrepareSplit + PrepareDocumentId.

    ASL utilisait States.StringSplit($.transaction.transactionId, 'trx-')
    puis States.Format('doc-{}', ...). En Python c'est trivial :
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


def _write_metadata_indexes(repo: Repo, payload: dict):
    """État Map WriteMetadataIndexes + Choice FilterEmptyMetadata.

    Le Map ASL (MaxConcurrency 10) avec son filtre devient soit une simple
    boucle (si volumes faibles), soit un group() Celery (si tu veux le
    parallélisme réel). Le filtre name/value non-vides est reproduit tel quel.
    """
    document_id = payload["document"]["documentId"]
    for item in payload.get("datas", []):
        name, value = item.get("name"), item.get("value")
        if name and value:  # = Choice FilterEmptyMetadata
            repo.put_metadata_index(document_id, name, value)
        # else: équivalent SkipMetadataIndex (no-op)


# ── Le workflow principal ────────────────────────────────────────────────────
@app.task(name="orchestration.ingestion.run", bind=True, acks_late=True)
def run_ingestion(self, payload: dict, repo_conn=None):
    """Point d'entrée — équivalent d'une exécution de l'IngestionStateMachine.

    Déclenché par ta Lambda IngestionTrigger (devenue une task ou un simple
    .delay() depuis l'événement S3/MinIO). `payload` = l'input de la SM.
    """
    repo = Repo(repo_conn)

    # ── CheckResumeMode (Choice) ─────────────────────────────────────────────
    if payload.get("resume_from") == "DISPATCH":
        # ResumeDispatch : documents/transactionId déjà dans l'input
        return _dispatch_processing(repo, payload)

    # ── CreateTransaction (Task) + Catch -> FailTransactionEarly ─────────────
    try:
        payload = create_transaction.apply(args=[payload]).get()
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
        document_id = payload["document"]["documentId"]
        tx_id = payload["transactionId"]

        # ── CreateDocument (dynamodb:updateItem) ─────────────────────────────
        repo.upsert_document(document_id, tx_id, payload["document"]["file"])

        # ── WriteMetadataIndexes (Map + Choice filter) ───────────────────────
        _write_metadata_indexes(repo, payload)

        # ── UpdateTransactionCreated : WORKING / DOC_CREATED ─────────────────
        repo.update_transaction(tx_id, status="WORKING", process="DOC_CREATED")

        # ── ExtractFiles -> CreateDocuments (deux Task séquentielles) ────────
        # En Celery : une chain. .apply().get() ici car on est déjà dans un
        # worker et on veut le résultat avant de continuer (= comportement
        # synchrone des états Task de l'ASL).
        payload = extract_files.apply(args=[payload]).get()
        payload = create_documents.apply(args=[payload]).get()

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
        repo.update_transaction(tx_id, status="ERROR")
        repo.add_message(tx_id, "ERROR", SOURCE, f"Error: {e}")
        raise


def _dispatch_processing(repo: Repo, payload: dict):
    """État DispatchProcessing (Type: Map, MaxConcurrency 10).

    ASL lançait un ProcessingStateMachine EXPRESS par document, en parallèle,
    avec startExecution.sync:2 (attend la fin) + Retry(2) + Catch ->
    MarkDocumentDispatchError.

    En Celery : un group() de la task d'orchestration Processing, une par
    document. Le parallélisme réel vient du nombre de workers sur la queue,
    pas d'un MaxConcurrency déclaratif (à régler via --concurrency du worker).
    """
    # Import tardif pour éviter le cycle (Processing importera des trucs d'ici)
    from orchestration.processing import run_processing

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
    # .apply_async() lance le parallélisme ; en prod on chaîne un callback
    # (chord) vers UpdateTransactionCompleted plutôt que d'attendre ici.
    result = job.apply_async()
    return result
