"""
Création des documents — portage de create_documents.py (ex-CreateDocumentsArn).

Migration souveraine : flowerscan_lib → alambic_core.
    optimistic_object(FclDocument, id)        → optimistic_object(Document, id)
    FclDocumentIndexRepository().batch_write  → DocumentIndexRepository + add_all
    FclDocumentIndexRepository().query_parent → DocumentIndexRepository.by_document
    FclTransactionIndexRepository (metadata)  → DocumentIndex index_type=metadata
    FclDocument.get_by_id / DEPRECATED        → DocumentRepository + status

Rôle (inchangé) : pour chaque document du payload, créer/mettre à jour l'entrée
Document, recopier les index du parent vers l'enfant, ajouter les index metadata
et extracted, puis déprécier le document parent après un split (avec
normalisation de l'id parent suffixé _0000N).

Idempotent : rejouable sans créer de doublons (clé (document_id, index_key)
côté DocumentIndex, upsert côté Document). Compatible avec acks_late de Celery.
"""

from __future__ import annotations

import re

from alambic_core.db.session import session_scope
from alambic_core.models import Document, DocumentIndex
from alambic_core.repositories import DocumentIndexRepository, DocumentRepository

from alambic_workers.optimistic import optimistic_object


def normalize_index(index: dict) -> dict:
    """Uniformise les deux conventions de nommage rencontrées dans le pipeline.

    Les index arrivent soit au format "payload" (name/value/description), soit au
    format "stocké" (index_name/index_value/index_desc). On normalise vers le
    premier. Repris tel quel de l'original.
    """
    return {
        "name": index.get("name") or index.get("index_name"),
        "value": index.get("value") or index.get("index_value"),
        "description": index.get("description") or index.get("index_desc"),
    }


def _copy_parent_indexes(session, parent_id: str, child_id: str) -> int:
    """Recopie les index du document parent vers l'enfant.

    Équivaut à : docrepo.query_parent(e.documentId) → batch_write(child).
    Utilisé après un split : l'enfant hérite des index du parent.
    """
    idx_repo = DocumentIndexRepository(session)
    parents = idx_repo.by_document(parent_id)
    copied = [
        DocumentIndex(
            document_id=child_id,
            index_name=p.index_name,
            index_value=p.index_value,
            index_desc=p.index_desc,
            index_type=p.index_type,
            index_score="1",
        )
        for p in parents
    ]
    session.add_all(copied)
    return len(copied)


def _add_indexes(session, child_id: str, items: list[dict], index_type: str) -> int:
    """Ajoute une liste d'index (metadata ou extracted) au document enfant.

    Équivaut aux deux blocs batch_write de update_document (file["datas"] pour
    metadata, file["indexes"] pour extracted).
    """
    indexes = []
    for raw in items:
        norm = normalize_index(raw)
        if not norm["name"]:
            continue
        score = "1"
        if index_type == "extracted":
            score = str(norm.get("score") or raw.get("index_score") or "1")
        indexes.append(
            DocumentIndex(
                document_id=child_id,
                index_name=norm["name"],
                index_value=norm["value"] or "",
                index_desc=norm["description"] or "",
                index_type=index_type,
                index_score=score,
            )
        )
    session.add_all(indexes)
    return len(indexes)


def update_document(
    session,
    *,
    parent_document_id: str,
    file: dict,
    process: str | None = None,
    process_state: str | None = None,
) -> dict | None:
    """Crée/met à jour un document enfant et ses index. Renvoie le doc traité.

    Reproduit update_document de l'original :
      - pose transaction/bucket/key/status,
      - recopie les index du parent,
      - ajoute metadata (file["datas"]) et extracted (file["indexes"]),
      - renvoie {documentId, bucket, key, file:{…}} si OK, None si erreur.
    """
    doc_id = file.get("documentId")
    if not doc_id:
        raise ValueError(
            f"update_document: 'documentId' manquant dans le fichier {file!r}. "
            "Chaque document du payload doit porter un documentId."
        )
    file_ref = file.get("file") or {}
    bucket = file.get("bucket") or file_ref.get("bucket")
    key = file.get("key") or file_ref.get("key")
    transaction_id = file.get("transactionId") or file_ref.get("transactionId")

    with optimistic_object(Document, doc_id, session=session) as document:
        document.transaction_id = transaction_id
        document.bucket_name = bucket
        document.object_key = key
        document.status = "ERROR" if file.get("error_code") else "OK"
        if process:
            document.process = process
        if process_state:
            document.process_state = process_state

    # Recopie des index parent + metadata + extracted
    _copy_parent_indexes(session, parent_document_id, doc_id)
    if file.get("datas"):
        _add_indexes(session, doc_id, file["datas"], "metadata")
    if file.get("indexes"):
        _add_indexes(session, doc_id, file["indexes"], "extracted")

    if file.get("error_code"):
        return None
    return {
        "documentId": doc_id,
        "bucket": bucket,
        "key": key,
        "file": {"bucket": bucket, "key": key},
    }


def process_documents(payload: dict) -> dict:
    """Point d'entrée — équivalent du handler de create_documents.py.

    Parcourt payload["documents"], crée chaque document + ses index, puis
    déprécie le document parent (to_delete) après un split.
    """
    documents_input = payload.get("documents", [])
    parent_document_id = payload.get("documentId") or payload.get("document", {}).get("documentId")
    process = payload.get("process")
    process_state = payload.get("process_state")

    created = []
    with session_scope() as session:
        # NOTE (à trancher) : l'original recopiait les meta_datas du 1er document
        # comme index de TRANSACTION (FclTransactionIndexRepository). alambic_core
        # n'a pas encore de modèle TransactionIndex distinct — soit on en ajoute un,
        # soit on rattache ces métadonnées autrement. Laissé volontairement de côté
        # tant que ce choix de schéma n'est pas fait, pour ne pas écrire du faux.
        # TODO: décider du sort des index de transaction (cf. discussion schéma).

        for file in documents_input:
            result = update_document(
                session,
                parent_document_id=parent_document_id,
                file=file,
                process=process,
                process_state=process_state,
            )
            if result:
                created.append(result)

        # ── Dépréciation du parent après split (to_delete) ───────────────────
        to_delete = payload.get("to_delete")
        if to_delete:
            _deprecate_parent(session, to_delete, {d["documentId"] for d in created})

    payload["documents"] = created or documents_input
    return payload


def _deprecate_parent(session, to_delete: str, processed_ids: set[str]) -> None:
    """Déprécie le document parent, avec normalisation de l'id suffixé _0000N.

    Reproduit la logique de l'original : on tente l'id tel quel (s'il n'est pas
    un doc qu'on vient de créer), puis l'id sans suffixe _0000N. Si trouvé et pas
    déjà DEPRECATED, on le marque DEPRECATED.
    """
    repo = DocumentRepository(session)
    candidates = []
    if to_delete not in processed_ids:
        candidates.append(to_delete)
    stripped = re.sub(r"_\d{4,5}$", "", to_delete)
    if stripped and stripped != to_delete and stripped not in processed_ids:
        candidates.append(stripped)

    for cand in candidates:
        doc = repo.get(cand)
        if doc is None:
            continue
        if doc.status != "DEPRECATED":
            doc.status = "DEPRECATED"
        return
