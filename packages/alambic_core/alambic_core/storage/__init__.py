"""
alambic_core.storage — accès au stockage objet Garage (S3-compatible).

Socle partagé entre alambic_workers (pipeline) et alambic_ui (dépôt depuis
l'admin). Garage expose une API S3, donc boto3 fonctionne tel quel : on pointe
l'endpoint_url vers Garage et on fournit les clés applicatives.

Config via variables d'environnement (souveraineté : self-hosted FR) :
    ALAMBIC_S3_ENDPOINT     ex. http://garage:3900
    ALAMBIC_S3_ACCESS_KEY   clé applicative Garage
    ALAMBIC_S3_SECRET_KEY   secret applicatif Garage
    ALAMBIC_S3_REGION       région déclarée (garage par défaut)
    ALAMBIC_S3_INPUT_BUCKET bucket d'entrée (zone __uploads__)
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache

# Préfixe de la zone d'upload (déclencheur d'ingestion). La clé complète suit le
# format __uploads__/<accountId>/<configId>/<origin>/<filename>, parsé par
# alambic_workers.tasks.start_ingestion.parse_upload_key.
UPLOADS_PREFIX = "__uploads__"
# Origine reconnue par start_ingestion.ORIGIN_PREFIXES pour un dépôt via l'admin.
DEFAULT_ORIGIN = "UI_IMPORT"

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_s3_client():
    """Client boto3 S3 pointé sur Garage (mémoïsé : un par process)."""
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("ALAMBIC_S3_ENDPOINT"),
        aws_access_key_id=os.environ.get("ALAMBIC_S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("ALAMBIC_S3_SECRET_KEY"),
        region_name=os.environ.get("ALAMBIC_S3_REGION", "garage"),
    )


def input_bucket() -> str:
    """Bucket d'entrée (zone d'upload). Configurable via env."""
    return os.environ.get("ALAMBIC_S3_INPUT_BUCKET", "alambic-input")


def work_bucket() -> str:
    """Bucket de travail (zone __transactions__). Configurable via env.

    C'est ici que vivent les fichiers de travail d'une transaction (source, PDF
    convertis, documents enfants). À ne pas confondre avec le bucket d'entrée.
    """
    return os.environ.get("ALAMBIC_S3_WORK_BUCKET", "alambic-work")


def build_upload_key(
    account_id: str, config_id: str, filename: str, origin: str = DEFAULT_ORIGIN
) -> str:
    """Construit la clé d'upload au format attendu par l'ingestion.

    __uploads__/<accountId>/<configId>/<origin>/<filename>
    """
    return f"{UPLOADS_PREFIX}/{account_id}/{config_id}/{origin}/{filename}"


def put_object(bucket: str, key: str, body_path: str, metadata: dict | None = None) -> None:
    """Dépose un fichier local (body_path) dans Garage."""
    client = get_s3_client()
    with open(body_path, "rb") as f:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=f,
            Metadata={k: str(v) for k, v in (metadata or {}).items()},
        )


def put_bytes(bucket: str, key: str, content: bytes, metadata: dict | None = None) -> None:
    """Dépose un contenu en mémoire dans Garage (utile pour l'upload web)."""
    client = get_s3_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=content,
        Metadata={k: str(v) for k, v in (metadata or {}).items()},
    )


def get_bytes(bucket: str, key: str) -> bytes:
    """Lit un objet Garage en mémoire (symétrique de put_bytes).

    Lève une exception si l'objet n'existe pas (le code appelant décide quoi
    faire — ex. le vector store traite l'absence comme 'pas encore de modèle').
    """
    client = get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def download_to(bucket: str, key: str, dest_path: str) -> str:
    """Télécharge un objet Garage vers un fichier local (dest_path).

    Crée les dossiers parents si besoin. Renvoie dest_path.
    """
    client = get_s3_client()
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    client.download_file(bucket, key, dest_path)
    return dest_path


def list_objects(bucket: str, prefix: str = "") -> list[dict]:
    """Liste les objets d'un bucket sous un préfixe.

    Renvoie une liste de dicts {key, size, last_modified}. Gère la pagination.
    """
    client = get_s3_client()
    out: list[dict] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            out.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj.get("LastModified"),
                }
            )
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return out


def list_common_prefixes(bucket: str, prefix: str = "") -> list[str]:
    """Liste les 'sous-dossiers' immédiats sous un préfixe (via Delimiter='/').

    Renvoie les CommonPrefixes : utile pour énumérer comptes/configs/transactions
    sans lister tous les objets. Gère la pagination.
    """
    client = get_s3_client()
    out: list[str] = []
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix, "Delimiter": "/"}
        if token:
            kwargs["ContinuationToken"] = token
        resp = client.list_objects_v2(**kwargs)
        for cp in resp.get("CommonPrefixes", []):
            p = cp.get("Prefix")
            if p:
                out.append(p)
        if resp.get("IsTruncated"):
            token = resp.get("NextContinuationToken")
        else:
            break
    return out


def delete_object(bucket: str, key: str) -> None:
    """Supprime un objet (utile pour vider __uploads__ après déclenchement)."""
    get_s3_client().delete_object(Bucket=bucket, Key=key)


def delete_prefix(bucket: str, prefix: str) -> int:
    """Supprime tous les objets d'un bucket sous un préfixe. Renvoie le nombre réellement supprimé.

    Robuste face aux implémentations S3 alternatives (Garage) :
      - tente la suppression par lots (delete_objects) en lisant les erreurs
        renvoyées par clé (pas de Quiet : on veut voir les échecs) ;
      - bascule sur une suppression objet-par-objet (delete_object, universelle)
        pour les clés en échec, ou pour tout le lot si delete_objects n'est pas
        supporté ;
      - ne compte que les objets effectivement supprimés (pas une estimation).
    """
    if not prefix:
        raise ValueError("delete_prefix exige un préfixe non vide (sécurité)")
    client = get_s3_client()
    objects = list_objects(bucket, prefix=prefix)
    if not objects:
        return 0

    deleted = 0
    for i in range(0, len(objects), 1000):
        batch = objects[i : i + 1000]
        keys = [o["key"] for o in batch]
        failed_keys = []

        try:
            resp = client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": k} for k in keys], "Quiet": False},
            )
            # Compte les suppressions confirmées + récupère les erreurs par clé.
            confirmed = {d.get("Key") for d in resp.get("Deleted", [])}
            errors = {e.get("Key") for e in resp.get("Errors", [])}
            # Certaines implémentations (dont Garage selon config) ne renvoient
            # pas "Deleted" même en cas de succès : si ni Deleted ni Errors, on
            # vérifiera par relecture plus bas plutôt que de présumer.
            if confirmed or errors:
                deleted += len(confirmed)
                failed_keys = [k for k in keys if k in errors]
            else:
                failed_keys = keys  # statut indéterminé → on revérifie en unitaire
        except Exception as exc:  # noqa: BLE001
            # delete_objects non supporté ou en échec → repli unitaire complet.
            logger.warning("delete_objects indisponible (%s), repli unitaire", exc)
            failed_keys = keys

        # Repli / vérification : suppression unitaire des clés non confirmées.
        for key in failed_keys:
            try:
                client.delete_object(Bucket=bucket, Key=key)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.error("Échec suppression objet %s : %s", key, exc)

    # Vérification finale : le préfixe doit être vide.
    remaining = list_objects(bucket, prefix=prefix)
    if remaining:
        logger.error(
            "delete_prefix %s : %d objet(s) subsistent après suppression",
            prefix,
            len(remaining),
        )

    return deleted
