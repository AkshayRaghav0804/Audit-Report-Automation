"""Portable string enums used by the persistence model."""

from __future__ import annotations

from enum import StrEnum


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class StageType(StrEnum):
    MANAGER = "manager"
    DIRECTOR = "director"
    PARTNER = "partner"
    QC = "qc"
    EQCR = "eqcr"
    CUSTOM = "custom"


class ExecutionMode(StrEnum):
    SEQUENTIAL = "sequential"
    PARALLEL = "parallel"


class CompletionPolicy(StrEnum):
    ALL = "all"
    ANY = "any"
    THRESHOLD = "threshold"


class AssignmentSource(StrEnum):
    REPORT_SELECTION = "report_selection"
    FIXED_USER = "fixed_user"
    REPORTING_LINE = "reporting_line"


class AnswerRequirement(StrEnum):
    REQUIRED_ONLY = "required_only"
    ALL_APPLICABLE = "all_applicable"
    CUSTOM = "custom"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    RETURNED = "returned"
    REJECTED = "rejected"
    APPROVED = "approved"
    FINALIZED = "finalized"
    ARCHIVED = "archived"
    CANCELLED = "cancelled"


class RevisionStatus(StrEnum):
    EDITABLE = "editable"
    SUBMITTED = "submitted"
    FINALIZED = "finalized"


class ApprovalCycleStatus(StrEnum):
    ACTIVE = "active"
    RETURNED = "returned"
    REJECTED = "rejected"
    APPROVED = "approved"
    CANCELLED = "cancelled"


class ApprovalStageStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    APPROVED = "approved"
    RETURNED = "returned"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class AssignmentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    APPROVED = "approved"
    RETURNED = "returned"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    REASSIGNED = "reassigned"


class ApprovalActionType(StrEnum):
    SUBMITTED = "submitted"
    APPROVED = "approved"
    RETURNED = "returned"
    REJECTED = "rejected"
    REASSIGNED = "reassigned"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ImportKind(StrEnum):
    PEOPLE = "people"
    REPORT_TEMPLATES = "report_templates"
    QUESTIONS = "questions"
    WORKFLOWS = "workflows"
    MASTER_DATA = "master_data"


class ImportStatus(StrEnum):
    PREVIEWED = "previewed"
    COMMITTED = "committed"
    FAILED = "failed"
    CANCELLED = "cancelled"
