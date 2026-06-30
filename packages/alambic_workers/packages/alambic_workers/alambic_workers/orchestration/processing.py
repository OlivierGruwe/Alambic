"""alambic_workers.orchestration.processing — traitement par document en CHAÎNE.

Chaque étape lourde du pipeline est une tâche Celery autonome, routée vers sa
propre queue, qui traite son étape PUIS déclenche la suivante. Modèle FlowerScan
(une Lambda par étape) transposé en Celery.

Avantage : scalabilité par étape. On met plus de workers OCR (goulot coûteux)
que de workers CAB (léger), en réglant le nombre de répliques de chaque service
Docker. Chaque queue = un pool de workers indépendant.

Chaîne des étapes (queue entre parenthèses) :

    run_processing (normal)
        -> convert        (office si Office, sinon normal)
        -> read_cab       (cab)
        -> read_ocr       (ocr)
        -> detect_split   (normal)   [decoupage : brique F]
        -> classify       (classif)
        -> extract_fields (extract)
        -> finalize       (normal)   [validation + export]

Chaque tâche est encadrée par `step` (MAJ DB, durée, rejouabilité : une étape
déjà faite est sautée à la reprise), enchaîne la suivante en fin de traitement,
et s'arrête si le document a été écarté (payload["document"] is None).

L'état circule dans le payload (+ la base). Aucune tâche n'attend une autre de
façon synchrone (pas de .get() : anti-pattern Celery).
"""

from __future__ import annotations

import logging

from alambic_workers.celery_app import app
from alambic_workers.conversion import is_office_key

logger = logging.getLogger(__name__)


def _normalize_payload(payload: dict) -> dict:
    """Aligne le payload du dispatch sur la structure attendue par les étapes."""
    if "transaction" not in payload:
        payload["transaction"] = {"transactionId": payload.get("transactionId")}
    return payload


def _document_active(payload: dict) -> bool:
    """False si le document a été écarté (DISCARDED) en cours de route."""
    return payload.get("document") is not None


def _convert_queue(payload: dict) -> str:
    """Queue de la conversion : office pour le bureautique, normal sinon."""
    file_key = (payload.get("document") or {}).get("file", {}).get("key", "")
    return "office" if is_office_key(file_key) else "normal"


# -- Point d'entree : premiere tache de la chaine ----------------------------
@app.task(name="alambic_workers.processing.run", bind=True, acks_late=True)
def run_processing(self, payload: dict) -> dict:
    """Démarre le traitement d'un document : lance la conversion (étape 1)."""
    payload = _normalize_payload(payload)
    doc_id = (payload.get("document") or {}).get("documentId")
    logger.info(
        "Traitement document %s (transaction %s)",
        doc_id,
        payload["transaction"]["transactionId"],
    )
    convert.apply_async(args=[payload], queue=_convert_queue(payload))
    return {"started": doc_id}


# -- Etape : CONVERSION au format pivot PDF [CABLE] --------------------------
@app.task(name="alambic_workers.processing.convert", bind=True, acks_late=True)
def convert(self, payload: dict) -> dict:
    """Convertit le document en PDF, puis enchaîne la lecture CAB."""
    from alambic_workers.tasks.conversion import convert_document

    payload = convert_document(payload)
    if not _document_active(payload):
        logger.info("Document écarté à la conversion, chaîne arrêtée")
        return payload
    read_cab.apply_async(args=[payload], queue="cab")
    return payload


# -- Etape : LECTURE CODES-BARRES [A VENIR - brique E, partie CAB] -----------
@app.task(name="alambic_workers.processing.read_cab", bind=True, acks_late=True)
def read_cab(self, payload: dict) -> dict:
    """Lit les codes-barres (local, léger), puis enchaîne l'OCR.

    Gating dans read_cab_document : ne lit que si le doctype a un bcr_type.
    """
    from alambic_workers.tasks.barcode import read_cab_document

    if not _document_active(payload):
        return payload
    payload = read_cab_document(payload)
    read_ocr.apply_async(args=[payload], queue="ocr")
    return payload


# -- Etape : OCR [A VENIR - brique E, partie OCR] ----------------------------
@app.task(name="alambic_workers.processing.read_ocr", bind=True, acks_late=True)
def read_ocr(self, payload: dict) -> dict:
    """OCR du PDF (moteur hybride), puis enchaîne le découpage.

    Extraction dans read_ocr_document : texte natif + OCR EdenAI sélectif,
    barcodes réinjectés, markdown + lignes positionnées persistés.
    """
    from alambic_workers.tasks.ocr import read_ocr_document

    if not _document_active(payload):
        return payload
    payload = read_ocr_document(payload)
    detect_split.apply_async(args=[payload], queue="normal")
    return payload


# -- Etape : DECOUPAGE [A VENIR - brique F] ----------------------------------
@app.task(name="alambic_workers.processing.detect_split", bind=True, acks_late=True)
def detect_split(self, payload: dict) -> dict:
    """Découpe le document en documents logiques, puis enchaîne la classification.

    Si découpage : chaque enfant repart en classification individuellement
    (filiation parent_id). Sinon le document continue tel quel.
    """
    from alambic_workers.tasks.split import split_document

    if not _document_active(payload):
        return payload
    payload = split_document(payload)

    children = payload.get("children") or []
    if children:
        # Document découpé : chaque enfant poursuit la chaîne séparément.
        for child in children:
            child_payload = dict(payload)
            child_payload["document"] = {
                "documentId": child["documentId"],
                "file": child["file"],
            }
            child_payload.pop("children", None)
            # Les barcodes du parent (toutes pages) ne doivent pas fuiter vers
            # l'enfant : ses propres barcodes (filtrés par page) sont déjà sur
            # son document. On retire l'héritage pour éviter toute confusion.
            child_payload.pop("barcodes", None)
            classify.apply_async(args=[child_payload], queue="classif")
        return payload

    # Pas de découpage : le document continue tel quel.
    classify.apply_async(args=[payload], queue="classif")
    return payload


# -- Etape : CLASSIFICATION IA [A VENIR - brique G, partie classif] ----------
@app.task(name="alambic_workers.processing.classify", bind=True, acks_late=True)
def classify(self, payload: dict) -> dict:
    """Classe le document (cascade lexical→embedding→LLM), puis enchaîne l'extraction.

    Sur panne externe transitoire (LLM/EdenAI injoignable : auth, rate-limit,
    5xx, timeout), la classification est relancée automatiquement avec backoff
    plutôt que de faire tomber le document en échec.
    """
    from alambic_core.pipeline.step import TransientStepError

    from alambic_workers.tasks.classify import classify_document

    if not _document_active(payload):
        return payload
    try:
        payload = classify_document(payload)
    except TransientStepError as exc:
        # Backoff exponentiel plafonné : 60s, 120s, 240s… max 10 tentatives.
        delay = min(60 * (2**self.request.retries), 3600)
        logger.warning(
            "Classification reportée (panne externe), retry dans %ds : %s", delay, exc
        )
        raise self.retry(exc=exc, countdown=delay, max_retries=10) from exc
    extract_fields.apply_async(args=[payload], queue="extract")
    return payload


# -- Etape : EXTRACTION DE CHAMPS IA [A VENIR - brique G, partie extract] ----
@app.task(name="alambic_workers.processing.extract_fields", bind=True, acks_late=True)
def extract_fields(self, payload: dict) -> dict:
    """Extrait les champs (conventionnel + LLM EdenAI), puis enchaîne la finalisation.

    Coûteux (appel LLM) → queue dédiée 'extract'.
    """
    if not _document_active(payload):
        return payload
    from alambic_workers.tasks.extract import extract_document

    payload = extract_document(payload)
    finalize.apply_async(args=[payload], queue="normal")
    return payload


# -- Etape : VALIDATION / EXPORT [A VENIR] -----------------------------------
@app.task(name="alambic_workers.processing.finalize", bind=True, acks_late=True)
def finalize(self, payload: dict) -> dict:
    """Validation puis export. Posera status=EXPORTED + exported_at. [À VENIR]

    Étape terminale : déclenchera la rétention (via exported_at). Pas de suite.
    """
    if not _document_active(payload):
        return payload
    doc_id = (payload.get("document") or {}).get("documentId")
    logger.info("Traitement terminé pour le document %s", doc_id)
    return payload


@app.task(name="alambic_workers.processing.export_document", bind=True, acks_late=True)
def export_document_task(self, doc_id: str) -> dict:
    """Exporte un document validé (web service ou S3 sortant).

    Déclenchée après la validation humaine. Asynchrone : l'export peut être lent
    (réseau), il ne doit pas bloquer l'UI.
    """
    from alambic_workers.tasks.export import export_document

    return export_document(doc_id)
