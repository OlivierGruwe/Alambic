"""
Accès au stockage objet Garage via boto3 (compatible S3).

Remplace create_s3_obj / FclBoto de flowerscan_lib. Garage expose une API
S3-compatible, donc boto3 fonctionne tel quel : il suffit de pointer
l'endpoint_url vers Garage et de fournir les clés applicatives.

Config via variables d'environnement (souveraineté : tout self-hosted FR) :
    ALAMBIC_S3_ENDPOINT     ex. http://garage:3900
    ALAMBIC_S3_ACCESS_KEY   clé applicative Garage
    ALAMBIC_S3_SECRET_KEY   secret applicatif Garage
    ALAMBIC_S3_REGION       région déclarée (garage par défaut)
"""

from __future__ import annotations

import os
from functools import lru_cache

import boto3


@lru_cache(maxsize=1)
def get_s3_client():
    """Client boto3 S3 pointé sur Garage (mémoïsé).

    lru_cache : un seul client réutilisé par process worker (boto3 est
    thread-safe). Évite de reconstruire un client par appel.
    """
    return boto3.client(
        "s3",
        endpoint_url=os.environ.get("ALAMBIC_S3_ENDPOINT"),
        aws_access_key_id=os.environ.get("ALAMBIC_S3_ACCESS_KEY"),
        aws_secret_access_key=os.environ.get("ALAMBIC_S3_SECRET_KEY"),
        region_name=os.environ.get("ALAMBIC_S3_REGION", "garage"),
    )


def put_object(bucket: str, key: str, body_path: str, metadata: dict | None = None) -> None:
    """Dépose un fichier local (body_path) dans Garage. Équiv. create_s3_obj."""
    client = get_s3_client()
    with open(body_path, "rb") as f:
        client.put_object(
            Bucket=bucket,
            Key=key,
            Body=f,
            Metadata={k: str(v) for k, v in (metadata or {}).items()},
        )
