"""Permission-protected, database-backed administrative audit history."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import func, select

from app.config import Settings
from app.database.models.actor import ApplicationUser
from app.database.models.approval import ApprovalAction, ReportApprovalStage
from app.database.models.import_data import MasterDataImportBatch
from app.database.models.report import (
    Report,
    ReportAnswer,
    ReportAnswerHistory,
    ReportRevision,
    ReportSectionChange,
)
from app.database.models.template import QuestionDefinition, TemplateQuestion, TemplateSection
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.authorization_service import require_permission
from app.services.permission_service import has_permission

AUDIT_PERMISSION = "audit_log.view"
DEFAULT_AUDIT_LIMIT = 250
MAX_AUDIT_LIMIT = 500


@dataclass(frozen=True, slots=True)
class AuditHistoryEvent:
    """A display-safe projection of one persisted application event."""

    id: str
    occurred_at: datetime
    category: str
    action: str
    actor_name: str
    actor_email: str
    actor_role: str | None
    report_public_id: str | None
    report_title: str | None
    subject: str
    details: str
    correlation_id: str | None = None


class AuditHistoryService:
    """Combine the application's persisted audit sources into one recent feed."""

    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def load(
        self,
        profile: User,
        *,
        limit: int = DEFAULT_AUDIT_LIMIT,
    ) -> tuple[AuditHistoryEvent, ...]:
        demo_auth = Settings.from_environment().demo_auth_enabled
        if demo_auth and not has_permission(profile, AUDIT_PERMISSION):
            raise PermissionError(f"{AUDIT_PERMISSION} is required to view audit history.")
        bounded_limit = max(1, min(int(limit), MAX_AUDIT_LIMIT))

        async with self.manager.session() as session:
            if not demo_auth:
                await require_permission(session, profile, AUDIT_PERMISSION)
            events = [
                *await _report_creation_events(session, bounded_limit),
                *await _approval_events(session, bounded_limit),
                *await _answer_change_events(session, bounded_limit),
                *await _section_change_events(session, bounded_limit),
                *await _import_events(session, bounded_limit),
            ]

        events.sort(key=lambda event: _utc_sort_value(event.occurred_at), reverse=True)
        return tuple(events[:bounded_limit])


async def _report_creation_events(session: Any, limit: int) -> list[AuditHistoryEvent]:
    rows = await session.execute(
        select(
            Report.id,
            Report.created_at,
            Report.public_id,
            Report.title,
            Report.entity_name,
            ApplicationUser.display_name,
            ApplicationUser.email,
        )
        .join(ApplicationUser, ApplicationUser.id == Report.created_by_user_id)
        .order_by(Report.created_at.desc())
        .limit(limit)
    )
    return [
        AuditHistoryEvent(
            id=f"report:{row[0]}",
            occurred_at=row[1],
            category="Report",
            action="Created",
            actor_name=row[5],
            actor_email=row[6],
            actor_role=None,
            report_public_id=row[2],
            report_title=row[3],
            subject=row[4] or row[3],
            details="Report record created.",
        )
        for row in rows
    ]


async def _approval_events(session: Any, limit: int) -> list[AuditHistoryEvent]:
    rows = await session.execute(
        select(
            ApprovalAction.id,
            ApprovalAction.action_at,
            ApprovalAction.action,
            ApprovalAction.actor_role_snapshot,
            ApprovalAction.previous_state,
            ApprovalAction.resulting_state,
            ApprovalAction.notes,
            ApprovalAction.correlation_id,
            Report.public_id,
            Report.title,
            ApplicationUser.display_name,
            ApplicationUser.email,
            ReportApprovalStage.display_name_snapshot,
        )
        .join(Report, Report.id == ApprovalAction.report_id)
        .join(ApplicationUser, ApplicationUser.id == ApprovalAction.actor_user_id)
        .outerjoin(
            ReportApprovalStage,
            ReportApprovalStage.id == ApprovalAction.report_approval_stage_id,
        )
        .order_by(ApprovalAction.action_at.desc())
        .limit(limit)
    )
    events: list[AuditHistoryEvent] = []
    for row in rows:
        transition = ""
        if row[4]:
            transition = f"{_humanize(row[4])} to {_humanize(row[5])}."
        else:
            transition = f"Resulting state: {_humanize(row[5])}."
        events.append(
            AuditHistoryEvent(
                id=f"approval:{row[0]}",
                occurred_at=row[1],
                category="Approval",
                action=_humanize(row[2]),
                actor_name=row[10],
                actor_email=row[11],
                actor_role=_humanize(row[3]),
                report_public_id=row[8],
                report_title=row[9],
                subject=row[12] or "Approval workflow",
                details=_join_details(transition, row[6]),
                correlation_id=row[7],
            )
        )
    return events


async def _answer_change_events(session: Any, limit: int) -> list[AuditHistoryEvent]:
    rows = await session.execute(
        select(
            ReportAnswerHistory.id,
            ReportAnswerHistory.changed_at,
            ReportAnswerHistory.actor_label_snapshot,
            ReportAnswerHistory.actor_role_snapshot,
            ReportAnswerHistory.previous_value_json,
            ReportAnswerHistory.new_value_json,
            ReportAnswerHistory.reason,
            ReportAnswerHistory.correlation_id,
            Report.public_id,
            Report.title,
            ApplicationUser.display_name,
            ApplicationUser.email,
            QuestionDefinition.label,
        )
        .join(ReportAnswer, ReportAnswer.id == ReportAnswerHistory.report_answer_id)
        .join(
            TemplateQuestion,
            TemplateQuestion.id == ReportAnswer.template_question_id,
        )
        .join(QuestionDefinition, QuestionDefinition.id == TemplateQuestion.question_id)
        .join(ReportRevision, ReportRevision.id == ReportAnswerHistory.report_revision_id)
        .join(Report, Report.id == ReportRevision.report_id)
        .join(ApplicationUser, ApplicationUser.id == ReportAnswerHistory.changed_by_user_id)
        .order_by(ReportAnswerHistory.changed_at.desc())
        .limit(limit)
    )
    return [
        AuditHistoryEvent(
            id=f"answer:{row[0]}",
            occurred_at=row[1],
            category="Report content",
            action="Answer changed",
            actor_name=row[2] or row[10],
            actor_email=row[11],
            actor_role=_optional_humanize(row[3]),
            report_public_id=row[8],
            report_title=row[9],
            subject=row[12],
            details=_join_details(
                f"Before: {_value_preview(row[4])} | After: {_value_preview(row[5])}",
                row[6],
            ),
            correlation_id=row[7],
        )
        for row in rows
    ]


async def _section_change_events(session: Any, limit: int) -> list[AuditHistoryEvent]:
    rows = await session.execute(
        select(
            ReportSectionChange.id,
            ReportSectionChange.changed_at,
            ReportSectionChange.actor_label_snapshot,
            ReportSectionChange.actor_role_snapshot,
            func.substr(ReportSectionChange.previous_content, 1, 161),
            func.substr(ReportSectionChange.new_content, 1, 161),
            ReportSectionChange.reason,
            Report.public_id,
            Report.title,
            ApplicationUser.email,
            TemplateSection.title,
            ReportSectionChange.stage_label_snapshot,
            ReportSectionChange.change_target,
        )
        .join(Report, Report.id == ReportSectionChange.report_id)
        .join(ApplicationUser, ApplicationUser.id == ReportSectionChange.changed_by_user_id)
        .join(TemplateSection, TemplateSection.id == ReportSectionChange.template_section_id)
        .order_by(ReportSectionChange.changed_at.desc())
        .limit(limit)
    )
    return [
        AuditHistoryEvent(
            id=f"section:{row[0]}",
            occurred_at=row[1],
            category="Section permissions" if row[12] == "lock" else "Controlled wording",
            action="Lock changed" if row[12] == "lock" else "Section changed",
            actor_name=row[2],
            actor_email=row[9],
            actor_role=_optional_humanize(row[3]),
            report_public_id=row[7],
            report_title=row[8],
            subject=row[10],
            details=_join_details(
                f"Before: {_text_preview(row[4])} | After: {_text_preview(row[5])}",
                f"Stage: {row[11]}",
                row[6],
            ),
        )
        for row in rows
    ]


async def _import_events(session: Any, limit: int) -> list[AuditHistoryEvent]:
    rows = await session.execute(
        select(
            MasterDataImportBatch.id,
            MasterDataImportBatch.created_at,
            MasterDataImportBatch.committed_at,
            MasterDataImportBatch.kind,
            MasterDataImportBatch.status,
            MasterDataImportBatch.filename,
            MasterDataImportBatch.inserted_count,
            MasterDataImportBatch.updated_count,
            MasterDataImportBatch.unchanged_count,
            MasterDataImportBatch.warning_count,
            MasterDataImportBatch.error_count,
            ApplicationUser.display_name,
            ApplicationUser.email,
        )
        .join(ApplicationUser, ApplicationUser.id == MasterDataImportBatch.uploaded_by_user_id)
        .order_by(
            func.coalesce(
                MasterDataImportBatch.committed_at,
                MasterDataImportBatch.created_at,
            ).desc()
        )
        .limit(limit)
    )
    return [
        AuditHistoryEvent(
            id=f"import:{row[0]}",
            occurred_at=row[2] or row[1],
            category="Master data",
            action=f"{_humanize(row[3])} import {_humanize(row[4]).casefold()}",
            actor_name=row[11],
            actor_email=row[12],
            actor_role=None,
            report_public_id=None,
            report_title=None,
            subject=row[5],
            details=(
                f"{row[6]} inserted, {row[7]} updated, {row[8]} unchanged, "
                f"{row[9]} warnings, {row[10]} errors."
            ),
        )
        for row in rows
    ]


def _utc_sort_value(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _humanize(value: object) -> str:
    raw = value.value if isinstance(value, Enum) else str(value)
    return raw.replace("_", " ").strip().title()


def _optional_humanize(value: object | None) -> str | None:
    return _humanize(value) if value else None


def _value_preview(value: object) -> str:
    if value is None:
        return "(empty)"
    if isinstance(value, str):
        return _text_preview(value)
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        rendered = str(value)
    return _text_preview(rendered)


def _text_preview(value: object, *, max_length: int = 160) -> str:
    rendered = " ".join(str(value or "").split()) or "(empty)"
    if len(rendered) <= max_length:
        return rendered
    return rendered[: max_length - 1].rstrip() + "…"


def _join_details(*parts: object | None) -> str:
    return " ".join(str(part).strip() for part in parts if part and str(part).strip())
