"""alambic_core.mail — ingestion par mail (IMAP)."""

from .imap_client import ImapClient, ImapParams, imap_params_from_config
from .parsing import SenderFilter, sender_filter_from_config, sender_of

__all__ = [
    "ImapClient",
    "ImapParams",
    "imap_params_from_config",
    "SenderFilter",
    "sender_filter_from_config",
    "sender_of",
]
