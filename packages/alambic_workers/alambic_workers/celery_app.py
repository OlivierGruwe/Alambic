"""
Application Celery centrale — remplace SQS + Step Functions (orchestration).

Broker      : RabbitMQ  (remplace SQS, gère le routing + les priorités)
Backend     : Redis     (stocke les résultats des tasks, pour chord/chain)
Souveraineté: les deux s'auto-hébergent en France (Scaleway/OVH/Outscale).

Les queues high/normal/low remplacent directement ton PriorityDispatcherFunction
+ tes 3 SQS (dispatch-high / dispatch-normal / dispatch-low). Plus besoin de
dispatcher custom : Celery route les tasks vers la bonne queue, et tu démarres
des workers dédiés par queue avec la concurrence que tu veux.

Au démarrage de chaque worker, on initialise alambic_core (moteur SQLAlchemy +
provider de chiffrement) via le signal worker_process_init — ainsi session_scope()
fonctionne dans les tâches sans configuration manuelle.
"""

import os

from celery import Celery
from celery.schedules import crontab
from celery.signals import worker_process_init
from kombu import Queue

BROKER_URL = os.environ.get("CELERY_BROKER", "amqp://guest:guest@rabbitmq:5672//")
RESULT_BACKEND = os.environ.get("CELERY_BACKEND", "redis://redis:6379/0")

app = Celery(
    "alambic",
    broker=BROKER_URL,
    backend=RESULT_BACKEND,
    # Import EXPLICITE des modules contenant des @app.task. On n'utilise pas
    # autodiscover_tasks : il cherche un sous-module nommé `tasks` dans chaque
    # package (ex. alambic_workers.orchestration.tasks), or nos tâches vivent
    # dans des modules nommés autrement (orchestration/ingestion.py). include
    # importe précisément les bons modules → les tâches sont enregistrées.
    include=[
        "alambic_workers.orchestration.ingestion",
        "alambic_workers.orchestration.processing",
        "alambic_workers.tasks.ingestion",
        "alambic_workers.tasks.conversion",
        "alambic_workers.tasks.retention",
        "alambic_workers.tasks.mail_poll",
        "alambic_workers.tasks.inbox_poll",
    ],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Europe/Paris",
    enable_utc=True,
    # Un seul ACK après exécution réussie : si un worker meurt en plein
    # traitement, la task repart sur un autre worker. C'est l'équivalent du
    # "at-least-once" de SQS. (Pense à l'idempotence de tes handlers.)
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Les 3 niveaux de priorité de ton pipeline (high/normal/low).
    # Remplacent les 3 SQS + le dispatcher.
    task_queues=(
        Queue("high", routing_key="high"),
        Queue("normal", routing_key="normal"),
        Queue("low", routing_key="low"),
        # Queues dédiées par étape lourde du pipeline. Chacune est consommée par
        # son propre pool de workers, scalable indépendamment (réplicas Docker).
        # Permet de mettre plus de workers OCR (goulot coûteux) que CAB (léger).
        Queue("office", routing_key="office"),  # conversion LibreOffice
        Queue("cab", routing_key="cab"),  # lecture codes-barres (local, léger)
        Queue("ocr", routing_key="ocr"),  # OCR EdenAI (coûteux, lent)
        Queue("multidoc", routing_key="multidoc"),  # détection multi-doc (vision Pixtral, coûteux)
        Queue("classif", routing_key="classif"),  # classification IA
        Queue("extract", routing_key="extract"),  # extraction de champs IA
    ),
    task_default_queue="normal",
    task_default_routing_key="normal",
    # Routing : chaque task métier va dans sa queue.
    task_routes={
        "alambic_workers.tasks.ingestion.*": {"queue": "normal"},
        "alambic_workers.tasks.processing.*": {"queue": "normal"},
        "alambic_workers.tasks.ai.*": {"queue": "normal"},
        "alambic_workers.orchestration.ingestion.*": {"queue": "normal"},
        "alambic_workers.orchestration.processing.*": {"queue": "normal"},
        # Ré-ingestion manuelle depuis l'UI (RetryPipeline) → priorité haute
        "alambic_workers.orchestration.retry.*": {"queue": "high"},
        # Conversion Office → queue dédiée (workers isolés LibreOffice).
        "alambic_workers.conversion.office": {"queue": "office"},
        # Détection multi-document → queue dédiée (appel vision Pixtral coûteux,
        # isolé pour ne pas bloquer les étapes légères de la queue normal).
        "alambic_workers.processing.multi_doc": {"queue": "multidoc"},
        # Relève des boîtes mail (IMAP) : I/O réseau, sur la queue normal.
        "alambic_workers.mail.poll": {"queue": "normal"},
        # Import des entrées FTP/S3 : I/O réseau, sur la queue normal.
        "alambic_workers.inbox.poll": {"queue": "normal"},
    },
    # Planification Beat : purge quotidienne des transactions dont la rétention
    # (par config, repli global) est écoulée. 3h du matin = heure creuse.
    beat_schedule={
        "purge-retention-quotidienne": {
            "task": "alambic_workers.retention.purge",
            "schedule": crontab(hour=3, minute=0),
        },
        # Rattrapage des exports en attente (échecs transitoires, configurations
        # tardives). Plus réactif que la rétention : un document validé ne doit
        # pas attendre longtemps. La Config est relue à chaque passage.
        "balayage-exports-en-attente": {
            "task": "alambic_workers.export.sweep",
            "schedule": crontab(minute="*/15"),
        },
        # Relève des boîtes mail (IMAP) : récupère les nouveaux mails et les
        # injecte dans le pipeline. Toutes les 5 min (compromis réactivité/charge).
        "releve-mail-imap": {
            "task": "alambic_workers.mail.poll",
            "schedule": crontab(minute="*/5"),
        },
        # Import des entrées FTP/S3 : liste les sources d'entrée des configs
        # actives, importe les nouveaux fichiers (dédoublonnage temporel par
        # fenêtre glissante) et les déplace vers treated/YYYYMMDD/. Toutes les
        # 5 min, aligné sur le mail.
        "import-entrees-ftp-s3": {
            "task": "alambic_workers.inbox.poll",
            "schedule": crontab(minute="*/5"),
        },
        # Voiture-balai : supprime les dossiers Garage orphelins (transactions
        # disparues de la base). Ménage de fond, hebdomadaire, nuit du dimanche.
        "voiture-balai-garage": {
            "task": "alambic_workers.storage.orphan_sweep",
            "schedule": crontab(hour=4, minute=0, day_of_week=0),
        },
        # Compaction vectorielle : agrège les embeddings des documents validés en
        # centroïdes de classification (apprentissage incrémental). Horaire.
        "compaction-vectorielle": {
            "task": "alambic_workers.vectors.compact",
            "schedule": crontab(minute=0),
        },
    },
)

# Les tâches sont importées explicitement via `include=[...]` ci-dessus
# (au constructeur Celery). Vérifie au démarrage que le worker affiche bien
# 'alambic_workers.ingestion.run' dans sa section [tasks].


@worker_process_init.connect
def _init_alambic_core(**_kwargs):
    """Initialise alambic_core dans chaque process worker.

    Configure le moteur SQLAlchemy et le provider Fernet à partir de
    ALAMBIC_DATABASE_URL et ALAMBIC_SECRET_KEY. Indispensable pour que
    session_scope() fonctionne dans les tâches (le Repo s'appuie dessus).
    """
    from alambic_core.db.session import init_core

    init_core()
