"""alambic_core — socle partagé du pipeline Alambic.

Modèles SQLAlchemy, accès données (repositories), et sécurité (chiffrement
des secrets). Remplace flowerscan_lib sur la partie persistance.
"""

from .db.base import Base

__all__ = ["Base"]
