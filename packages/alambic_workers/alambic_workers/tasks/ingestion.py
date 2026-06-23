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

import uuid

from alambic_core.db.session import session_scope
from alambic_core.models import Transaction
from alambic_core.repositories import TransactionRepository


def _new_transaction_id() -> str:
    """Génère un identifiant de transaction au format historique 'trx-...'."""
    return f"trx-{uuid.uuid4().hex[:16]}"


def create_transaction(payload: dict) -> dict:
    """État CreateTransaction — crée la transaction en base (alambic_core).

    Entrée  : payload de la machine d'état (cid, configId, accountId, documents…).
    Sortie  : payload enrichi de payload["transaction"]["transactionId"].

    La transaction démarre en statut WORKING / process NEWDOC. C'est l'ancre de
    l'état du workflow : tout le reste (documents, messages) s'y rattache.
    """
    tx_id = payload.get("transaction", {}).get("transactionId") or _new_transaction_id()

    with session_scope() as s:
        repo = TransactionRepository(s)
        tx = repo.get(tx_id)
        if tx is None:
            tx = Transaction(
                id=tx_id,
                status="WORKING",
                process="NEWDOC",
                account_id=payload.get("accountId"),
                config_id=payload.get("configId"),
                nb_docs=len(payload.get("documents", [])),
            )
            s.add(tx)

    # Remonte l'id dans le payload (comme le faisait l'état Task de l'ASL)
    payload.setdefault("transaction", {})["transactionId"] = tx_id
    return payload


def extract_files(payload: dict) -> dict:
    """État ExtractFiles (ExtractFilesArn) — SQUELETTE.

    Rôle attendu (d'après l'ASL FlowerScan) : lire l'objet source dans le bucket
    d'entrée (Garage), et si c'est une archive / un multi-page, en extraire les
    fichiers individuels vers le bucket de travail.

    TODO :
      1. Récupérer l'objet depuis Garage (bucket d'entrée) via boto3
         (endpoint = ALAMBIC_S3_ENDPOINT, clés = ALAMBIC_S3_ACCESS/SECRET_KEY).
      2. Détecter le type (zip, pdf multi-page, image…) et extraire.
      3. Écrire les fichiers extraits dans le bucket de travail.
      4. Renseigner payload["extracted_files"] = [{bucket, key}, ...].

    Pour l'instant : passe-plat (un seul fichier = le fichier source).
    """
    # TODO: brancher la vraie extraction (boto3 + Garage). Passe-plat en attendant.
    src = payload.get("document", {}).get("file")
    if src is not None:
        payload["extracted_files"] = [src]
    return payload


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
