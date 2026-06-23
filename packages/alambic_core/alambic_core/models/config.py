"""
alambic_core.models.config — modèle Config (paramétrage du pipeline).

Reprend fcl_config.FclConfig (~60 champs) mais ÉCLATÉ en blocs jsonb cohérents
plutôt qu'à plat. Plus lisible, plus maintenable. Chaque bloc regroupe un
domaine fonctionnel.

SECRETS : les blocs contenant des secrets (ftp_in/out, aws_in/out, edenai)
sont stockés CHIFFRÉS en entier (EncryptedString sur le JSON sérialisé). Le
code applicatif (re)sérialise ; voir la couche service/repository. Les champs
non sensibles restent en jsonb clair pour rester interrogeables.

Mapping des blocs ← champs flowerscan_lib :
  general    ← config_name, fixed_page, filter_extensions, need_validation,
               pdf_max_pages, multi_doc_detect, way_in, way_out, fallback_way_out
  ftp_in     ← ftp_*_in (+ password chiffré)
  ftp_out    ← ftp_*_out (+ password chiffré)
  aws_in     ← aws_*_in / fallback_aws_*_in (+ secret_key chiffré)
  aws_out    ← aws_*_out (+ secret_key chiffré)
  ws         ← ws_address, ws_token, ws_data_key, ws_doc_key
  flower     ← flower_url/user/scope (+ password chiffré)
  edenai     ← edenai_secret_key (chiffré), ocr_*, embedding_*, classifier_*,
               extract_* (endpoints, providers, models, limites)
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str
from ..db.types import EncryptedString


class Config(Base, TimestampMixin, AuditMixin):
    """Configuration d'un flux de traitement. Pas de versioning (config admin)."""

    __tablename__ = "configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    config_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    doctype_id: Mapped[str] = mapped_column(String(36), nullable=False, default="")
    need_validation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    multi_doc_detect: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # ── Blocs jsonb CLAIRS (pas de secret, interrogeables) ───────────────────
    # general : fixed_page, filter_extensions, pdf_max_pages, way_in/out…
    general: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # edenai_settings : tout le paramétrage IA NON secret (endpoints, providers,
    # models, max_chars, languages, confidence…). La clé edenai_secret_key
    # est dans le bloc chiffré ci-dessous.
    edenai_settings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # ws : adresses + clés non sensibles des web services de consolidation
    ws: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # ── Blocs CHIFFRÉS (JSON sérialisé chiffré au repos) ─────────────────────
    # Chacun contient les credentials d'un canal. Sérialisés/désérialisés par
    # la couche repository (helpers ci-dessous documentent la forme attendue).
    ftp_in_enc: Mapped[str] = mapped_column(EncryptedString(8192), nullable=False, default="")
    ftp_out_enc: Mapped[str] = mapped_column(EncryptedString(8192), nullable=False, default="")
    aws_in_enc: Mapped[str] = mapped_column(EncryptedString(8192), nullable=False, default="")
    aws_out_enc: Mapped[str] = mapped_column(EncryptedString(8192), nullable=False, default="")
    flower_enc: Mapped[str] = mapped_column(EncryptedString(8192), nullable=False, default="")
    edenai_secret_enc: Mapped[str] = mapped_column(
        EncryptedString(4096), nullable=False, default=""
    )

    # Relation
    account: Mapped["Account"] = relationship(back_populates="configs")

    def __repr__(self) -> str:
        return f"<Config {self.id} {self.config_name}>"
