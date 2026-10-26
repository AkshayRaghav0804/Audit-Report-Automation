"""Versioned report templates, sections, and questions."""

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
    Integer,
    String,
    Text,
    UniqueConstraint,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.enums import PublicationStatus
from app.database.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class ReportTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_templates"

    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(250), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )


class ReportTemplateVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "report_template_versions"
    __table_args__ = (
        UniqueConstraint("template_id", "version_number", name="uq_template_version_number"),
        UniqueConstraint("template_id", "source_sha256", name="uq_template_version_source_hash"),
    )

    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_templates.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[PublicationStatus] = mapped_column(
        Enum(
            PublicationStatus,
            name="report_template_version_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
        ),
        default=PublicationStatus.DRAFT,
        nullable=False,
    )
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    formatting_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )


class TemplateSection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "template_sections"
    __table_args__ = (
        UniqueConstraint("template_version_id", "section_key", name="uq_template_section_key"),
        UniqueConstraint(
            "template_version_id",
            "display_order",
            name="uq_template_section_order",
        ),
    )

    template_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    section_key: Mapped[str] = mapped_column(String(160), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    section_type: Mapped[str] = mapped_column(String(60), nullable=False, default="fixed")
    fixed_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    default_content: Mapped[str | None] = mapped_column(Text)
    editable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    default_locked: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
    formatting_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    visibility_rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class QuestionDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "question_definitions"

    key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    label: Mapped[str] = mapped_column(String(500), nullable=False)
    help_text: Mapped[str | None] = mapped_column(Text)
    input_type: Mapped[str] = mapped_column(String(60), nullable=False)
    options_json: Mapped[list[Any]] = mapped_column(JSON, default=list, nullable=False)
    validation_rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )


class TemplateQuestion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "template_questions"
    __table_args__ = (UniqueConstraint("template_version_id", "question_id"),)

    template_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("report_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    question_id: Mapped[UUID] = mapped_column(
        ForeignKey("question_definitions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    section_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("template_sections.id", ondelete="RESTRICT"), index=True
    )
    display_order: Mapped[int] = mapped_column(Integer, nullable=False)
    required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_value_json: Mapped[Any | None] = mapped_column(JSON)
    conditional_rules_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    mapping_rule_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=true(), nullable=False
    )
