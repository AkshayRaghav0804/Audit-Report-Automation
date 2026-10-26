"""Configurable and versioned approval workflow definitions."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.enums import (
    AnswerRequirement,
    AssignmentSource,
    CompletionPolicy,
    ExecutionMode,
    PublicationStatus,
    StageType,
)
from app.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ApprovalWorkflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_workflows"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )


class ApprovalWorkflowVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_workflow_versions"
    __table_args__ = (UniqueConstraint("workflow_id", "version_number"),)

    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_workflows.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(
            PublicationStatus,
            name="approval_workflow_version_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=PublicationStatus.DRAFT,
        nullable=False,
    )
    self_approval_allowed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReportTemplateWorkflow(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_template_workflows"
    __table_args__ = (UniqueConstraint("template_id", "workflow_id"),)

    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_templates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    workflow_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_workflows.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class ApprovalStepDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "approval_step_definitions"
    __table_args__ = (
        UniqueConstraint("workflow_version_id", "stage_code", name="uq_workflow_stage_code"),
        UniqueConstraint("workflow_version_id", "sequence", name="uq_workflow_stage_sequence"),
        CheckConstraint("min_assignees >= 0", name="min_assignees_nonnegative"),
        CheckConstraint(
            "max_assignees IS NULL OR max_assignees >= min_assignees",
            name="max_assignees_valid",
        ),
        CheckConstraint(
            "approval_threshold IS NULL OR approval_threshold > 0",
            name="approval_threshold_positive",
        ),
        CheckConstraint(
            "due_in_days IS NULL OR due_in_days > 0",
            name="approval_due_in_days_positive",
        ),
    )

    workflow_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("approval_workflow_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    stage_code: Mapped[str] = mapped_column(String(100), nullable=False)
    stage_type: Mapped[StageType] = mapped_column(
        Enum(
            StageType,
            name="approval_stage_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    required_role_key: Mapped[str] = mapped_column(String(60), nullable=False)
    required_permission: Mapped[str] = mapped_column(
        String(100), nullable=False, default="report.review"
    )
    fixed_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("application_users.id", ondelete="RESTRICT")
    )
    min_assignees: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_assignees: Mapped[int | None] = mapped_column(Integer)
    execution_mode: Mapped[ExecutionMode] = mapped_column(
        Enum(
            ExecutionMode,
            name="approval_execution_mode",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=ExecutionMode.SEQUENTIAL,
        nullable=False,
    )
    completion_policy: Mapped[CompletionPolicy] = mapped_column(
        Enum(
            CompletionPolicy,
            name="approval_completion_policy",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=CompletionPolicy.ALL,
        nullable=False,
    )
    approval_threshold: Mapped[int | None] = mapped_column(Integer)
    assignment_source: Mapped[AssignmentSource] = mapped_column(
        Enum(
            AssignmentSource,
            name="approval_assignment_source",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=AssignmentSource.REPORT_SELECTION,
        nullable=False,
    )
    answer_requirement: Mapped[AnswerRequirement] = mapped_column(
        Enum(
            AnswerRequirement,
            name="approval_answer_requirement",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=AnswerRequirement.REQUIRED_ONLY,
        nullable=False,
    )
    due_in_days: Mapped[int | None] = mapped_column(Integer)
    applicability_rule_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    return_policy_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
