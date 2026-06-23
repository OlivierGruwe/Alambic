"""alambic_core.models.doctype — modèle Doctype."""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import AuditMixin, Base, TimestampMixin, uuid_str


class Doctype(Base, TimestampMixin, AuditMixin):
    """Type de document. Reprend fcl_doctype.FclDoctype.

    Pas de versioning optimiste : table de config, modifiée par un admin,
    pas en concurrence par le pipeline. Timestamps + audit suffisent.
    """

    __tablename__ = "doctypes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    doctype_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    account_id: Mapped[str | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    json_content: Mapped[str] = mapped_column(Text, nullable=False, default="")

    def __repr__(self) -> str:
        return f"<Doctype {self.id} {self.doctype_name}>"
