"""Report-local section locks and attributed editing exceptions."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ReportSectionLock(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_section_locks"
    __table_args__ = (UniqueConstraint("report_id", "template_section_id"),)

    report_id: Mapped[UUID] = mapped_column(ForeignKey("reports.id"), nullable=False)
    template_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_sections.id"), nullable=False
    )
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    changed_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id"), nullable=False
    )


class SectionEditRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "section_edit_requests"

    report_id: Mapped[UUID] = mapped_column(ForeignKey("reports.id"), nullable=False, index=True)
    template_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_sections.id"), nullable=False
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    decided_by_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("application_users.id"))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
