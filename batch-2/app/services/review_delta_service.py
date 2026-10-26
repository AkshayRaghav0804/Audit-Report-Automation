"""Reviewer-facing change summaries for reports resubmitted after a return."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.database.models.actor import ApplicationUser
from app.database.models.approval import ApprovalAction, ApprovalCycle, ReportApprovalStage
from app.database.models.enums import ApprovalActionType, ApprovalCycleStatus, ReportStatus
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
from app.services.report_access_service import ReportAccess, ReportAccessService


@dataclass(frozen=True, slots=True)
class ReviewDeltaChange:
    """One net report value changed between return and resubmission."""

    target_key: str
    target_label: str
    section_key: str | None
    section_title: str
    previous_text: str
    new_text: str
    actor_label: str
    actor_role: str
    changed_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewDeltaSection:
    section_key: str | None
    title: str
    changes: tuple[ReviewDeltaChange, ...]
    display_order: int


@dataclass(frozen=True, slots=True)
class ReviewDelta:
    returned_at: datetime
    resubmitted_at: datetime
    returned_by: str
    returned_stage: str
    return_reason: str
    sections: tuple[ReviewDeltaSection, ...]

    @property
    def total_changes(self) -> int:
        return sum(len(section.changes) for section in self.sections)

    @property
    def report_sections(self) -> tuple[ReviewDeltaSection, ...]:
        return tuple(section for section in self.sections if section.section_key is not None)


@dataclass(frozen=True, slots=True)
class _DeltaEvent:
    target_key: str
    target_label: str
    section_key: str | None
    section_title: str
    section_order: int
    previous_text: str
    new_text: str
    actor_label: str
    actor_role: str
    changed_at: datetime


class ReviewDeltaService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager
        self.access = ReportAccessService(manager)

    async def load(
        self,
        public_id: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> ReviewDelta | None:
        """Return net corrections made after the latest return and before resubmission."""

        resolved_access = access or await self.access.access_for(public_id, profile)
        if resolved_access.report_public_id != public_id or not resolved_access.can_view:
            raise PermissionError("You do not have access to this report.")
        if (
            not resolved_access.can_approve
            or resolved_access.report_status is not ReportStatus.IN_REVIEW
        ):
            return None

        async with self.manager.session() as session:
            current = (
                await session.execute(
                    select(Report, ReportRevision, ApprovalCycle)
                    .join(
                        ReportRevision,
                        (ReportRevision.report_id == Report.id)
                        & (ReportRevision.revision_number == Report.current_revision_number),
                    )
                    .join(
                        ApprovalCycle,
                        ApprovalCycle.report_revision_id == ReportRevision.id,
                    )
                    .where(
                        Report.public_id == public_id,
                        ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
                    )
                    .order_by(ApprovalCycle.cycle_number.desc())
                    .limit(1)
                )
            ).one_or_none()
            if current is None:
                return None
            report, revision, current_cycle = current
            returned = (
                await session.execute(
                    select(
                        ApprovalAction.action_at,
                        ApprovalAction.notes,
                        ApplicationUser.display_name,
                        ReportApprovalStage.display_name_snapshot,
                    )
                    .join(ApplicationUser, ApplicationUser.id == ApprovalAction.actor_user_id)
                    .join(
                        ReportApprovalStage,
                        ReportApprovalStage.id == ApprovalAction.report_approval_stage_id,
                    )
                    .join(ApprovalCycle, ApprovalCycle.id == ApprovalAction.approval_cycle_id)
                    .where(
                        ApprovalAction.report_id == report.id,
                        ApprovalAction.report_revision_id == revision.id,
                        ApprovalAction.action == ApprovalActionType.RETURNED,
                        ApprovalCycle.status == ApprovalCycleStatus.RETURNED,
                        ApprovalAction.action_at < current_cycle.submitted_at,
                    )
                    .order_by(ApprovalAction.action_at.desc(), ApprovalAction.id.desc())
                    .limit(1)
                )
            ).one_or_none()
            if returned is None:
                return None
            returned_at, return_reason, returned_by, returned_stage = returned
            events = [
                *_answer_events(
                    await session.execute(
                        select(
                            QuestionDefinition.key,
                            QuestionDefinition.label,
                            TemplateSection.section_key,
                            TemplateSection.title,
                            TemplateSection.display_order,
                            ReportAnswerHistory.previous_value_json,
                            ReportAnswerHistory.new_value_json,
                            ReportAnswerHistory.actor_label_snapshot,
                            ReportAnswerHistory.actor_role_snapshot,
                            ReportAnswerHistory.changed_at,
                        )
                        .join(
                            ReportAnswer,
                            ReportAnswer.id == ReportAnswerHistory.report_answer_id,
                        )
                        .join(
                            TemplateQuestion,
                            TemplateQuestion.id == ReportAnswer.template_question_id,
                        )
                        .join(
                            QuestionDefinition,
                            QuestionDefinition.id == TemplateQuestion.question_id,
                        )
                        .outerjoin(
                            TemplateSection,
                            TemplateSection.id == TemplateQuestion.section_id,
                        )
                        .where(
                            ReportAnswerHistory.report_revision_id == revision.id,
                            ReportAnswerHistory.changed_at > returned_at,
                            ReportAnswerHistory.changed_at <= current_cycle.submitted_at,
                        )
                        .order_by(
                            ReportAnswerHistory.changed_at,
                            ReportAnswerHistory.id,
                        )
                    )
                ),
                *_section_events(
                    await session.execute(
                        select(
                            TemplateSection.section_key,
                            TemplateSection.title,
                            TemplateSection.display_order,
                            ReportSectionChange.change_target,
                            ReportSectionChange.previous_content,
                            ReportSectionChange.new_content,
                            ReportSectionChange.actor_label_snapshot,
                            ReportSectionChange.actor_role_snapshot,
                            ReportSectionChange.changed_at,
                        )
                        .join(
                            TemplateSection,
                            TemplateSection.id == ReportSectionChange.template_section_id,
                        )
                        .where(
                            ReportSectionChange.report_revision_id == revision.id,
                            ReportSectionChange.change_target.in_(("content", "heading")),
                            ReportSectionChange.changed_at > returned_at,
                            ReportSectionChange.changed_at <= current_cycle.submitted_at,
                        )
                        .order_by(
                            ReportSectionChange.changed_at,
                            ReportSectionChange.id,
                        )
                    )
                ),
            ]

        sections = _collapse_events(events)
        if not sections:
            return None
        return ReviewDelta(
            returned_at=returned_at,
            resubmitted_at=current_cycle.submitted_at,
            returned_by=str(returned_by),
            returned_stage=str(returned_stage),
            return_reason=str(return_reason or "No return reason recorded."),
            sections=sections,
        )


def _answer_events(rows: Any) -> tuple[_DeltaEvent, ...]:
    return tuple(
        _DeltaEvent(
            target_key=f"answer:{field_name}",
            target_label=str(label),
            section_key=str(section_key) if section_key is not None else None,
            section_title=str(section_title or "Report details"),
            section_order=int(section_order) if section_order is not None else -1,
            previous_text=_display_value(previous_value),
            new_text=_display_value(new_value),
            actor_label=str(actor_label or "Unknown editor"),
            actor_role=str(actor_role or "Contributor"),
            changed_at=changed_at,
        )
        for (
            field_name,
            label,
            section_key,
            section_title,
            section_order,
            previous_value,
            new_value,
            actor_label,
            actor_role,
            changed_at,
        ) in rows
    )


def _section_events(rows: Any) -> tuple[_DeltaEvent, ...]:
    return tuple(
        _DeltaEvent(
            target_key=f"section:{section_key}:{target}",
            target_label="Section heading" if target == "heading" else "Section wording",
            section_key=str(section_key),
            section_title=str(section_title),
            section_order=int(section_order),
            previous_text=str(previous_content),
            new_text=str(new_content),
            actor_label=str(actor_label),
            actor_role=str(actor_role or "Contributor"),
            changed_at=changed_at,
        )
        for (
            section_key,
            section_title,
            section_order,
            target,
            previous_content,
            new_content,
            actor_label,
            actor_role,
            changed_at,
        ) in rows
    )


def _collapse_events(events: list[_DeltaEvent]) -> tuple[ReviewDeltaSection, ...]:
    grouped: dict[str, list[_DeltaEvent]] = {}
    for event in sorted(events, key=lambda item: item.changed_at):
        grouped.setdefault(event.target_key, []).append(event)

    net_changes: list[ReviewDeltaChange] = []
    section_orders: dict[str, int] = {}
    for target_events in grouped.values():
        first = target_events[0]
        latest = target_events[-1]
        if first.previous_text == latest.new_text:
            continue
        section_group = first.section_key or ""
        section_orders[section_group] = first.section_order
        net_changes.append(
            ReviewDeltaChange(
                target_key=first.target_key,
                target_label=first.target_label,
                section_key=first.section_key,
                section_title=first.section_title,
                previous_text=first.previous_text,
                new_text=latest.new_text,
                actor_label=latest.actor_label,
                actor_role=latest.actor_role,
                changed_at=latest.changed_at,
            )
        )

    by_section: dict[str, list[ReviewDeltaChange]] = {}
    for change in net_changes:
        by_section.setdefault(change.section_key or "", []).append(change)
    sections = [
        ReviewDeltaSection(
            section_key=changes[0].section_key,
            title=changes[0].section_title,
            changes=tuple(sorted(changes, key=lambda item: item.changed_at)),
            display_order=section_orders[section_key],
        )
        for section_key, changes in by_section.items()
    ]
    return tuple(sorted(sections, key=lambda item: (item.display_order, item.title.casefold())))


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "(empty)"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
