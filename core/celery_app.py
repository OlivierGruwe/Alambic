"""
Application Celery centrale — remplace SQS + Step Functions (orchestration).

Broker      : RabbitMQ  (remplace SQS, gère le routing + les priorités)
Backend     : Redis     (stocke les résultats des tasks, pour chord/chain)
Souveraineté: les deux s'auto-hébergent en France (Scaleway/OVH/Outscale).

Les queues high/normal/low remplacent directement ton PriorityDispatcherFunction
+ tes 3 SQS (dispatch-high / dispatch-normal / dispatch-low). Plus besoin de
dispatcher custom : Celery route les tasks vers la bonne queue, et tu démarres
des workers dédiés par queue avec la concurrence que tu veux.
"""

import os

from celery import Celery
from kombu import Queue

BROKER_URL = os.environ.get("CELERY_BROKER", "amqp://guest:guest@rabbitmq:5672//")
RESULT_BACKEND = os.environ.get("CELERY_BACKEND", "redis://redis:6379/0")

app = Celery("flowerscan", broker=BROKER_URL, backend=RESULT_BACKEND)

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
    # Routing : chaque task métier va dans sa queue. Exemple de mapping ;
    # à enrichir avec tes vraies tasks Processing/AI.
    task_routes={
        "tasks.ingestion.*": {"queue": "normal"},
        "tasks.processing.*": {"queue": "normal"},
        "tasks.ai.*": {"queue": "normal"},
        # Ré-ingestion manuelle depuis l'UI (RetryPipeline) → priorité haute
        "orchestration.retry.*": {"queue": "high"},
    },
)
