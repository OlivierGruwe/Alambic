"""
alambic_workers.storage — ré-export du client storage partagé.

Le client Garage vit désormais dans alambic_core.storage (socle partagé UI +
workers). Ce module reste pour compatibilité : start_ingestion et le code
existant peuvent continuer à `from alambic_workers import storage`.
"""

from __future__ import annotations

from alambic_core.storage import (  # noqa: F401
    build_upload_key,
    delete_object,
    delete_prefix,
    download_to,
    get_s3_client,
    input_bucket,
    list_objects,
    put_bytes,
    put_object,
)
