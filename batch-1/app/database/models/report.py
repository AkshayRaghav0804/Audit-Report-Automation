"""Persistent reports, revisions, signatories, and answers."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    text,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.enums import ReportStatus, RevisionStatus
from app.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class Report(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_status_last_activity", "status", "last_activity_at"),
        Index("ix_reports_owner_status", "owner_user_id", "status"),
    )

    public_id: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    template_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    workflow_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_workflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    engagement_code: Mapped[str | None] = mapped_column(String(100), index=True)
    entity_name: Mapped[str | None] = mapped_column(String(300), index=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[ReportStatus] = mapped_column(
        Enum(
            ReportStatus,
            name="report_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ReportStatus.DRAFT,
        nullable=False,
        index=True,
    )
    current_revision_number: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        server_default=text("CURRENT_TIMESTAMP"),
        nullable=False,
        index=True,
    )

    __mapper_args__ = {"version_id_col": row_version}


class ReportRevision(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_revisions"
    __table_args__ = (UniqueConstraint("report_id", "revision_number"),)

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_revision_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT")
    )
    status: Mapped[RevisionStatus] = mapped_column(
        Enum(
            RevisionStatus,
            name="report_revision_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=RevisionStatus.EDITABLE,
        nullable=False,
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportWorkflowAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Draft-time approver choices, later snapshotted into an approval cycle."""

    __tablename__ = "report_workflow_assignments"
    __table_args__ = (
        UniqueConstraint(
            "report_id",
            "step_definition_id",
            "user_id",
            name="uq_report_workflow_assignment_user",
        ),
        UniqueConstraint(
            "report_id",
            "step_definition_id",
            "assignment_order",
            name="uq_report_workflow_assignment_order",
        ),
    )

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    step_definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_step_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assignment_order: Mapped[int] = mapped_column(Integer, nullable=False)
    role_key_snapshot: Mapped[str] = mapped_column(String(60), nullable=False)
    display_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    selected_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )


class ReportSignatory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_signatories"
    __table_args__ = (UniqueConstraint("report_id", "user_id"),)

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    display_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    professional_body_snapshot: Mapped[str] = mapped_column(String(50), nullable=False)
    membership_number_snapshot: Mapped[str] = mapped_column(String(50), nullable=False)
    assigned_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )


class ReportAnswer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_answers"
    __table_args__ = (UniqueConstraint("report_revision_id", "template_question_id"),)

    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_question_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_questions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    row_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    __mapper_args__ = {"version_id_col": row_version}


class ReportAnswerHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "report_answer_history"

    report_answer_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_answers.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    previous_value_json: Mapped[Any | None] = mapped_column(JSON)
    new_value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    changed_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)
    actor_label_snapshot: Mapped[str | None] = mapped_column(String(200))
    actor_role_snapshot: Mapped[str | None] = mapped_column(String(120))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100))


class ReportAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A conditional supporting file attached to one editable report revision."""

    __tablename__ = "report_attachments"
    __table_args__ = (
        UniqueConstraint("report_revision_id", "attachment_kind"),
        Index("ix_report_attachments_revision_kind", "report_revision_id", "attachment_kind"),
    )

    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    attachment_kind: Mapped[str] = mapped_column(String(30), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(150), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )


class ReportSectionContent(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_section_contents"
    __table_args__ = (UniqueConstraint("report_revision_id", "template_section_id"),)

    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_sections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    edited_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )


class ReportSectionHeading(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Current report-specific override for a controlled section heading."""

    __tablename__ = "report_section_headings"
    __table_args__ = (UniqueConstraint("report_revision_id", "template_section_id"),)

    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_sections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    heading: Mapped[str] = mapped_column(String(300), nullable=False)
    heading_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    edited_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )


class ReportSectionChange(UUIDPrimaryKeyMixin, Base):
    """Immutable report-specific history for controlled wording amendments."""

    __tablename__ = "report_section_changes"
    __table_args__ = (
        Index(
            "ix_report_section_changes_revision_section",
            "report_revision_id",
            "template_section_id",
        ),
    )

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    template_section_id: Mapped[UUID] = mapped_column(
        ForeignKey("template_sections.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_approval_stage_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("report_approval_stages.id", ondelete="RESTRICT"),
        index=True,
    )
    previous_content: Mapped[str] = mapped_column(Text, nullable=False)
    new_content: Mapped[str] = mapped_column(Text, nullable=False)
    change_target: Mapped[str] = mapped_column(
        String(20), default="content", server_default="content", nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    actor_label_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    actor_role_snapshot: Mapped[str | None] = mapped_column(String(120))
    stage_label_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )


class ReportComment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_comments"
    __table_args__ = (Index("ix_report_comments_report_section", "report_id", "section_key"),)

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_approval_stage_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("report_approval_stages.id", ondelete="RESTRICT"), index=True
    )
    section_key: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    selected_text: Mapped[str | None] = mapped_column(Text)
    selection_start: Mapped[int | None] = mapped_column(Integer)
    author_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
