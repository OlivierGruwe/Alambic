"""alambic_core.services — opérations métier réutilisables (UI, workers, CLI)."""

from .deletion import DeletionResult, delete_transaction, transaction_work_prefix
from .retention import (
    config_retention_days,
    find_purgeable_transactions,
    global_retention_days,
    purge_expired_transactions,
)

__all__ = [
    "delete_transaction",
    "DeletionResult",
    "transaction_work_prefix",
    "purge_expired_transactions",
    "find_purgeable_transactions",
    "config_retention_days",
    "global_retention_days",
]
