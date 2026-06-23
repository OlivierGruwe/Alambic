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
from celery.signals import worker_process_init
from kombu import Queue

BROKER_URL = os.environ.get("CELERY_BROKER", "amqp://guest:guest@rabbitmq:5672//")
RESULT_BACKEND = os.environ.get("CELERY_BACKEND", "redis://redis:6379/0")

app = Celery("alambic", broker=BROKER_URL, backend=RESULT_BACKEND)

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
    },
)

# Découverte automatique des tâches dans les sous-packages tasks/ et orchestration/.
app.autodiscover_tasks(["alambic_workers.tasks", "alambic_workers.orchestration"], force=True)


@worker_process_init.connect
def _init_alambic_core(**_kwargs):
    """Initialise alambic_core dans chaque process worker.

    Configure le moteur SQLAlchemy et le provider Fernet à partir de
    ALAMBIC_DATABASE_URL et ALAMBIC_SECRET_KEY. Indispensable pour que
    session_scope() fonctionne dans les tâches (le Repo s'appuie dessus).
    """
    from alambic_core.db.session import init_core

    init_core()
