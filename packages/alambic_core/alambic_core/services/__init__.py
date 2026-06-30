"""alambic_core.services — opérations métier réutilisables (UI, workers, CLI)."""

from .auto_validation import decide_validation_status
from .completeness import CompletenessResult, compute_completeness, doctype_ids_from_expected
from .config_fields import computed_tokens, resolve_config_field, resolve_config_fields
from .consolidation import enrich_indexes, parse_target_field
from .consolidation_client import call_consolidation_ws
from .consolidation_ws import (
    normalize_ws_definition,
    validate_all,
    validate_ws_definition,
    ws_by_name,
)
from .config_duplication import duplicate_config
from .deletion import DeletionResult, delete_transaction, transaction_work_prefix
from .export_sweep import find_pending_exports, sweep_exports
from .orphan_sweep import OrphanSweepResult, sweep_orphans
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
    "find_pending_exports",
    "sweep_exports",
    "sweep_orphans",
    "OrphanSweepResult",
    "compute_completeness",
    "CompletenessResult",
    "doctype_ids_from_expected",
    "duplicate_config",
    "decide_validation_status",
    "resolve_config_fields",
    "resolve_config_field",
    "computed_tokens",
    "enrich_indexes",
    "parse_target_field",
    "call_consolidation_ws",
    "validate_ws_definition",
    "validate_all",
    "normalize_ws_definition",
    "ws_by_name",
]
