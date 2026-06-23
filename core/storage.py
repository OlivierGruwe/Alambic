"""Client de stockage objet (MinIO via boto3).

À implémenter : un wrapper boto3 pointant sur l'endpoint MinIO (au lieu d'AWS).
Ton code S3 existant fonctionne en changeant juste endpoint_url + les clés.
Expose get/put/delete/list sur les buckets input/work/storage.
"""
