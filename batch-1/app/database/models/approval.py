"""Per-report approval cycles, stage groups, assignments, and actions."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.enums import (
    AnswerRequirement,
    ApprovalActionType,
    ApprovalCycleStatus,
    ApprovalStageStatus,
    AssignmentStatus,
    CompletionPolicy,
    ExecutionMode,
)
from app.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class ApprovalCycle(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_cycles"
    __table_args__ = (
        UniqueConstraint("report_revision_id", "cycle_number"),
        Index("ix_approval_cycles_status_revision", "status", "report_revision_id"),
    )

    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    cycle_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ApprovalCycleStatus] = mapped_column(
        Enum(
            ApprovalCycleStatus,
            name="approval_cycle_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ApprovalCycleStatus.ACTIVE,
        nullable=False,
    )
    submitted_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resume_stage_code: Mapped[str | None] = mapped_column(String(100))


class ReportApprovalStage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_approval_stages"
    __table_args__ = (
        UniqueConstraint("approval_cycle_id", "stage_code", name="uq_runtime_stage_code"),
        UniqueConstraint("approval_cycle_id", "sequence", name="uq_runtime_stage_sequence"),
        CheckConstraint("min_assignees >= 0", name="runtime_min_assignees_nonnegative"),
        CheckConstraint(
            "max_assignees IS NULL OR max_assignees >= min_assignees",
            name="runtime_max_assignees_valid",
        ),
        Index("ix_report_approval_stages_status_due", "status", "due_at"),
    )

    approval_cycle_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_cycles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    step_definition_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_step_definitions.id", ondelete="RESTRICT"), nullable=False
    )
    stage_code: Mapped[str] = mapped_column(String(100), nullable=False)
    display_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    required_role_key_snapshot: Mapped[str] = mapped_column(String(60), nullable=False)
    required_permission_snapshot: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[ApprovalStageStatus] = mapped_column(
        Enum(
            ApprovalStageStatus,
            name="report_approval_stage_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ApprovalStageStatus.PENDING,
        nullable=False,
        index=True,
    )
    min_assignees: Mapped[int] = mapped_column(Integer, nullable=False)
    max_assignees: Mapped[int | None] = mapped_column(Integer)
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        Enum(
            ExecutionMode,
            name="runtime_approval_execution_mode",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    completion_policy: Mapped[CompletionPolicy] = mapped_column(
        Enum(
            CompletionPolicy,
            name="runtime_approval_completion_policy",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    approval_threshold: Mapped[int | None] = mapped_column(Integer)
    answer_requirement: Mapped[AnswerRequirement] = mapped_column(
        Enum(
            AnswerRequirement,
            name="runtime_approval_answer_requirement",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    skip_reason: Mapped[str | None] = mapped_column(String(300))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportStageAssignment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_stage_assignments"
    __table_args__ = (
        UniqueConstraint("report_approval_stage_id", "user_id", name="uq_stage_assignment_user"),
        UniqueConstraint(
            "report_approval_stage_id",
            "assignment_order",
            name="uq_stage_assignment_order",
        ),
        Index("ix_report_stage_assignments_user_status", "user_id", "status"),
    )

    report_approval_stage_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_approval_stages.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    assignment_order: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name_snapshot: Mapped[str] = mapped_column(String(200), nullable=False)
    email_snapshot: Mapped[str] = mapped_column(String(320), nullable=False)
    role_key_snapshot: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[AssignmentStatus] = mapped_column(
        Enum(
            AssignmentStatus,
            name="report_stage_assignment_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=AssignmentStatus.PENDING,
        nullable=False,
        index=True,
    )
    assigned_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False
    )
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalAction(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "approval_actions"

    report_id: Mapped[UUID] = mapped_column(
        ForeignKey("reports.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_revisions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    approval_cycle_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_cycles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    report_approval_stage_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("report_approval_stages.id", ondelete="RESTRICT"), index=True
    )
    assignment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("report_stage_assignments.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    actor_role_snapshot: Mapped[str] = mapped_column(String(60), nullable=False)
    action: Mapped[ApprovalActionType] = mapped_column(
        Enum(
            ApprovalActionType,
            name="approval_action_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    previous_state: Mapped[str | None] = mapped_column(String(60))
    resulting_state: Mapped[str] = mapped_column(String(60), nullable=False)
    action_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False, index=True
    )
    correlation_id: Mapped[str | None] = mapped_column(String(100))
