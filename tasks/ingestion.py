"""
Tasks Celery de l'Ingestion — une @app.task par Lambda de l'ASL.

Correspondance directe avec ton template.yaml :
    CreateTransactionFunction  -> create_transaction
    CreateDocumentsFunction    -> create_documents
    ExtractFilesFunction       -> extract_files

Le corps de chaque task EST le handler de ta Lambda actuelle, quasi inchangé :
même code Python, même logique métier. Seules différences :
  - signature : on reçoit/retourne un dict (l'"input/output" de l'état ASL)
    au lieu de (event, context).
  - retries : décorés ici (autoretry_for / retry_backoff) au lieu d'être dans
    le bloc "Retry" de l'ASL ou la RetryPolicy EventBridge.

Le `bind=True` donne accès à self.retry et au contexte (request.id = l'équivalent
de l'execution ID Step Functions, utile pour tracer dans la table messages).
"""

from core.celery_app import app

# Exceptions "réessayables" : timeouts réseau (EdenAI), throttling DB, etc.
# Équivalent du "Retry: ErrorEquals" de l'ASL. Les erreurs métier définitives
# ne sont PAS listées ici → elles tombent direct dans le Catch (cf. workflow).
RETRYABLE = (ConnectionError, TimeoutError)


@app.task(
    name="tasks.ingestion.create_transaction",
    bind=True,
    autoretry_for=RETRYABLE,
    retry_backoff=2,  # backoff exponentiel, base 2s — cf. BackoffRate ASL
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def create_transaction(self, payload: dict) -> dict:
    """État CreateTransaction de l'ASL.

    Ton handler create_transaction.handler actuel va ici tel quel.
    Doit retourner le payload enrichi (transaction.transactionId, documents[],
    datas, cid, accountId, configId) — exactement comme aujourd'hui.
    """
    # === COLLE ICI le corps de create_transaction.handler ===
    # transaction = build_transaction(payload); repo.create(transaction)
    # payload["transaction"] = transaction
    # payload["documents"] = [...]  # avec documents[0].file
    return payload


@app.task(
    name="tasks.ingestion.extract_files",
    bind=True,
    autoretry_for=RETRYABLE,
    retry_backoff=2,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def extract_files(self, payload: dict) -> dict:
    """État ExtractFiles. Décompresse/éclate le fichier source en documents.
    Colle ici extract_files.handler. Retourne payload avec documents[] peuplé."""
    # === COLLE ICI le corps de extract_files.handler ===
    return payload


@app.task(
    name="tasks.ingestion.create_documents",
    bind=True,
    autoretry_for=RETRYABLE,
    retry_backoff=2,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def create_documents(self, payload: dict) -> dict:
    """État CreateDocuments. Colle ici create_documents.handler."""
    # === COLLE ICI le corps de create_documents.handler ===
    return payload
