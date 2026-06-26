"""alambic_workers.tasks.conversion — étape de conversion au format pivot PDF.

Chaque document actif est converti en PDF avant lecture (OCR/CAB). Le routage par
type décide du convertisseur :
- pdf : déjà au format pivot, rien à faire ;
- texte / image : conversion locale légère (queue normale) ;
- office : conversion LibreOffice (lourde) → tâche dédiée sur la queue « office »,
  consommée par des workers isolés et multipliables.

Tout est encadré par le mécanisme `step` (MAJ DB, durée, rejouabilité). Un type
non convertible écarte le document (DISCARDED) et fait remonter l'info.
"""

from __future__ import annotations

import logging
import os
import tempfile

from alambic_core.db.session import session_scope
from alambic_core.domain.enums import DocumentStatus
from alambic_core.models import Document, Message, Transaction
from alambic_core.pipeline import step

from alambic_workers import storage
from alambic_workers.celery_app import app
from alambic_workers.conversion import (
    convert_to_pdf,
    detect_kind,
)

logger = logging.getLogger(__name__)

# Process des étapes de conversion (cohérent avec PIPELINE_STEPS).
PROCESS_CONVERTED = "FILE_CONVERTED"


def convert_document(payload: dict) -> dict:
    """Convertit le document du payload en PDF (format pivot).

    payload["document"] = {documentId, file:{bucket,key}}. Après conversion, la
    clé du document pointe vers le PDF. Si le type est Office, on délègue à la
    tâche dédiée (queue « office ») sauf si on est déjà dessus.
    """
    tx_id = payload["transaction"]["transactionId"]
    doc = payload.get("document", {})
    doc_id = doc.get("documentId")
    file_info = doc.get("file", {})
    bucket = file_info.get("bucket", "")
    key = file_info.get("key", "")

    with step(tx_id, PROCESS_CONVERTED, document_id=doc_id) as st:
        if st.skipped:
            return payload

        work_dir = tempfile.mkdtemp(prefix="alambic_conv_")
        out_dir = tempfile.mkdtemp(prefix="alambic_conv_out_")
        local_src = os.path.join(work_dir, os.path.basename(key) or "source")
        storage.download_to(bucket, key, local_src)

        kind = detect_kind(local_src)

        # Type non convertible → on écarte le document (DISCARDED) + remontée.
        if kind == "unknown":
            _discard_document(tx_id, doc_id, f"Type non convertible : {os.path.basename(key)}")
            payload["document"] = None
            return payload

        try:
            pdf_path, nb_pages, _ = convert_to_pdf(local_src, out_dir)
        except Exception as exc:
            _discard_document(tx_id, doc_id, f"Conversion échouée : {exc}")
            payload["document"] = None
            raise

        # Nouvelle clé : même emplacement, extension .pdf.
        pdf_key = os.path.splitext(key)[0] + ".pdf"
        storage.put_object(bucket, pdf_key, pdf_path)

        with session_scope() as s:
            d = s.get(Document, doc_id)
            if d is not None:
                d.object_key = pdf_key
                d.status = DocumentStatus.CONVERTED_TO_PDF.value

        # Met à jour le payload pour les étapes suivantes (OCR…).
        payload["document"]["file"]["key"] = pdf_key
        payload["nb_pages"] = nb_pages

    return payload


def _discard_document(tx_id: str, doc_id: str, reason: str) -> None:
    """Écarte un document (DISCARDED) et fait remonter l'info à la transaction."""
    with session_scope() as s:
        d = s.get(Document, doc_id)
        if d is not None:
            d.status = DocumentStatus.DISCARDED.value
            d.discard_reason = reason
        s.add(
            Message(
                transaction_id=tx_id,
                level="WARNING",
                source="conversion",
                text=f"Document écarté (conversion) : {reason}",
            )
        )
        tx = s.get(Transaction, tx_id)
        if tx is not None:
            tx.nb_discarded = (tx.nb_discarded or 0) + 1


# ── Tâches Celery ────────────────────────────────────────────────────────────
@app.task(
    name="alambic_workers.conversion.run",
    bind=True,
    acks_late=True,
)
def run_conversion(self, payload: dict) -> dict:
    """Tâche de conversion. Route les documents Office vers la queue dédiée.

    Pour un document Office, on renvoie la tâche sur la queue « office » (workers
    isolés, LibreOffice installé). Les autres types sont convertis ici.
    """
    doc = payload.get("document", {})
    file_info = doc.get("file", {})
    key = file_info.get("key", "")

    # Détection légère par extension pour le routage (sans télécharger).
    ext = os.path.splitext(key)[1].lower()
    office_exts = {
        ".doc",
        ".docx",
        ".odt",
        ".rtf",
        ".xls",
        ".xlsx",
        ".ods",
        ".ppt",
        ".pptx",
        ".odp",
    }
    is_office = ext in office_exts

    # Si Office et qu'on n'est pas déjà sur la queue dédiée, on redirige.
    if is_office and self.request.delivery_info.get("routing_key") != "office":
        run_conversion_office.apply_async(args=[payload], queue="office")
        return {"routed": "office", "documentId": doc.get("documentId")}

    return convert_document(payload)


@app.task(
    name="alambic_workers.conversion.office",
    bind=True,
    acks_late=True,
)
def run_conversion_office(self, payload: dict) -> dict:
    """Conversion Office isolée (LibreOffice). Consommée par les workers « office »."""
    return convert_document(payload)
