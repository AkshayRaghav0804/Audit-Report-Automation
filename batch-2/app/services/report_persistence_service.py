"""Persistence boundary for report answers and their immutable change history."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.models.approval import (
    ApprovalCycle,
    ReportApprovalStage,
    ReportStageAssignment,
)
from app.database.models.enums import (
    ApprovalCycleStatus,
    ApprovalStageStatus,
    ReportStatus,
    RevisionStatus,
)
from app.database.models.mixins import utc_now
from app.database.models.report import (
    Report,
    ReportAnswer,
    ReportAnswerHistory,
    ReportRevision,
    ReportSignatory,
)
from app.database.models.template import (
    QuestionDefinition,
    ReportTemplate,
    ReportTemplateVersion,
    TemplateQuestion,
    TemplateSection,
)
from app.database.session import DatabaseManager, database
from app.services.report_edit_role_service import report_edit_role

MAX_ANSWER_JSON_BYTES = 100_000


@dataclass(frozen=True, slots=True)
class PersistedDraft:
    public_id: str
    template_key: str
    answers: dict[str, Any]
    workflow_answers: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AnswerChangeSummary:
    field_name: str
    section_key: str | None
    actor_label: str
    actor_role: str
    reason: str | None
    changed_at: datetime


class ReportPersistenceService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def load_draft(self, public_id: str, template_key: str) -> PersistedDraft:
        async with self.manager.session() as session:
            report = await session.scalar(
                select(Report)
                .join(
                    ReportTemplateVersion,
                    ReportTemplateVersion.id == Report.template_version_id,
                )
                .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
                .where(Report.public_id == public_id, ReportTemplate.key == template_key)
            )
            if report is None:
                raise ValueError("This report draft does not exist or uses another template.")
            revision = await _current_revision(session, report)
            rows = await session.execute(
                select(QuestionDefinition.key, ReportAnswer.value_json)
                .join(
                    TemplateQuestion,
                    TemplateQuestion.question_id == QuestionDefinition.id,
                )
                .join(
                    ReportAnswer,
                    ReportAnswer.template_question_id == TemplateQuestion.id,
                )
                .where(ReportAnswer.report_revision_id == revision.id)
            )
            signatory = await session.scalar(
                select(ReportSignatory)
                .where(ReportSignatory.report_id == report.id)
                .order_by(ReportSignatory.created_at)
            )
            workflow_answers = (
                {
                    "workflow_partner": signatory.display_name_snapshot,
                    "workflow_partner_icai_membership_number": (
                        signatory.membership_number_snapshot
                    ),
                }
                if signatory is not None
                else {}
            )
            return PersistedDraft(
                public_id=report.public_id,
                template_key=template_key,
                answers={key: value for key, value in rows},
                workflow_answers=workflow_answers,
            )

    async def save_answer(
        self,
        *,
        public_id: str,
        template_key: str,
        field_name: str,
        value: Any,
        actor_email: str,
        change_reason: str | None = None,
        actor_role: str | None = None,
    ) -> None:
        try:
            serialized_value = json.dumps(value, ensure_ascii=False)
        except (RecursionError, TypeError, ValueError) as error:
            raise ValueError("Report answers must contain valid JSON data.") from error
        if len(serialized_value.encode("utf-8")) > MAX_ANSWER_JSON_BYTES:
            raise ValueError("A report answer cannot exceed 100 KB.")
        normalized_reason = str(change_reason or "").strip()
        async with self.manager.session() as session:
            async with session.begin():
                actor = await session.scalar(
                    select(ApplicationUser).where(
                        ApplicationUser.email == actor_email.casefold(),
                        ApplicationUser.active.is_(True),
                    )
                )
                if actor is None:
                    raise ValueError("The current user is not active in the people directory.")
                report = await session.scalar(
                    select(Report)
                    .join(
                        ReportTemplateVersion,
                        ReportTemplateVersion.id == Report.template_version_id,
                    )
                    .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
                    .where(Report.public_id == public_id, ReportTemplate.key == template_key)
                )
                if report is None:
                    raise ValueError("The report draft was not found.")
                if not await _may_edit_report(session, report, actor):
                    raise PermissionError(
                        "Only people who can view this report can edit report content."
                    )
                if report.status not in {
                    ReportStatus.DRAFT,
                    ReportStatus.RETURNED,
                    ReportStatus.IN_REVIEW,
                }:
                    raise ValueError("This report is no longer editable.")
                actor_role = await report_edit_role(session, report, actor.id)
                revision = await _current_revision(session, report)
                allowed_revision_statuses = {RevisionStatus.EDITABLE, RevisionStatus.SUBMITTED}
                if revision.status not in allowed_revision_statuses:
                    raise ValueError("The current report revision is not editable.")
                template_question = await session.scalar(
                    select(TemplateQuestion)
                    .join(
                        QuestionDefinition,
                        QuestionDefinition.id == TemplateQuestion.question_id,
                    )
                    .where(
                        TemplateQuestion.template_version_id == report.template_version_id,
                        QuestionDefinition.key == field_name,
                        TemplateQuestion.active.is_(True),
                    )
                )
                if template_question is None and field_name.startswith("word_"):
                    template_question = await _add_source_question_compatibility_mapping(
                        session,
                        report.template_version_id,
                        field_name,
                    )
                if template_question is None:
                    raise ValueError(f"Unknown report question: {field_name}")
                answer = await session.scalar(
                    select(ReportAnswer).where(
                        ReportAnswer.report_revision_id == revision.id,
                        ReportAnswer.template_question_id == template_question.id,
                    )
                )
                previous_value: Any | None = None
                if answer is None:
                    answer = ReportAnswer(
                        report_revision_id=revision.id,
                        template_question_id=template_question.id,
                        value_json=value,
                        updated_by_user_id=actor.id,
                    )
                    session.add(answer)
                    await session.flush()
                else:
                    previous_value = answer.value_json
                    if previous_value == value:
                        if field_name == "name_of_entity":
                            report.entity_name = _entity_name(value)
                        return
                    answer.value_json = value
                    answer.updated_by_user_id = actor.id
                    await session.flush()
                report.last_activity_at = utc_now()
                if field_name == "name_of_entity":
                    report.entity_name = _entity_name(value)
                changed_at = await _next_answer_change_timestamp(session, answer.id)
                session.add(
                    ReportAnswerHistory(
                        report_answer_id=answer.id,
                        report_revision_id=revision.id,
                        previous_value_json=previous_value,
                        new_value_json=value,
                        changed_by_user_id=actor.id,
                        reason=normalized_reason or None,
                        actor_label_snapshot=(
                            "QC Team" if actor_role == "QC Reviewer" else actor.display_name
                        ),
                        actor_role_snapshot=actor_role,
                        changed_at=changed_at,
                    )
                )

    async def answer_changes(
        self,
        public_id: str,
        template_key: str,
    ) -> tuple[AnswerChangeSummary, ...]:
        """Return the latest attributed change for each Word field in the preview."""

        async with self.manager.session() as session:
            ranked_changes = (
                select(
                    QuestionDefinition.key.label("field_name"),
                    TemplateSection.section_key.label("section_key"),
                    ReportAnswerHistory.actor_label_snapshot.label("actor_label"),
                    ReportAnswerHistory.actor_role_snapshot.label("actor_role"),
                    ReportAnswerHistory.reason.label("reason"),
                    ReportAnswerHistory.changed_at.label("changed_at"),
                    func.row_number()
                    .over(
                        partition_by=QuestionDefinition.key,
                        order_by=(
                            ReportAnswerHistory.changed_at.desc(),
                            ReportAnswerHistory.id.desc(),
                        ),
                    )
                    .label("change_rank"),
                )
                .join(ReportAnswer, ReportAnswer.id == ReportAnswerHistory.report_answer_id)
                .join(
                    TemplateQuestion,
                    TemplateQuestion.id == ReportAnswer.template_question_id,
                )
                .join(
                    QuestionDefinition,
                    QuestionDefinition.id == TemplateQuestion.question_id,
                )
                .outerjoin(TemplateSection, TemplateSection.id == TemplateQuestion.section_id)
                .join(
                    ReportRevision,
                    ReportRevision.id == ReportAnswerHistory.report_revision_id,
                )
                .join(Report, Report.id == ReportRevision.report_id)
                .join(
                    ReportTemplateVersion,
                    ReportTemplateVersion.id == Report.template_version_id,
                )
                .join(
                    ReportTemplate,
                    ReportTemplate.id == ReportTemplateVersion.template_id,
                )
                .where(
                    Report.public_id == public_id,
                    ReportTemplate.key == template_key,
                    QuestionDefinition.key.startswith("word_"),
                )
                .subquery()
            )
            rows = await session.execute(
                select(
                    ranked_changes.c.field_name,
                    ranked_changes.c.section_key,
                    ranked_changes.c.actor_label,
                    ranked_changes.c.actor_role,
                    ranked_changes.c.reason,
                    ranked_changes.c.changed_at,
                )
                .where(ranked_changes.c.change_rank == 1)
                .order_by(ranked_changes.c.changed_at.desc())
            )
            return tuple(
                AnswerChangeSummary(
                    field_name=str(field_name),
                    section_key=str(section_key) if section_key is not None else None,
                    actor_label=str(actor_label or "Unknown editor"),
                    actor_role=str(actor_role or "Contributor"),
                    reason=str(reason) if reason else None,
                    changed_at=changed_at,
                )
                for field_name, section_key, actor_label, actor_role, reason, changed_at in rows
            )


def _entity_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if len(normalized) > 300:
        raise ValueError("Entity name cannot exceed 300 characters.")
    return normalized or None


async def _next_answer_change_timestamp(session: AsyncSession, answer_id: UUID) -> datetime:
    """Keep rapid edits strictly ordered on clocks with coarse timestamp resolution."""

    latest = await session.scalar(
        select(func.max(ReportAnswerHistory.changed_at)).where(
            ReportAnswerHistory.report_answer_id == answer_id
        )
    )
    current = utc_now()
    if latest is None:
        return current
    if latest.tzinfo is None:
        current = current.replace(tzinfo=None)
    if current <= latest:
        return latest + timedelta(microseconds=1)
    return current


async def _current_revision(session: AsyncSession, report: Report) -> ReportRevision:
    revision = await session.scalar(
        select(ReportRevision).where(
            ReportRevision.report_id == report.id,
            ReportRevision.revision_number == report.current_revision_number,
        )
    )
    if revision is None:
        raise RuntimeError("The report has no current revision.")
    return revision


async def _add_source_question_compatibility_mapping(
    session: AsyncSession,
    template_version_id: UUID,
    field_name: str,
) -> TemplateQuestion | None:
    """Backfill a newly parsed Word field onto an older immutable report version.

    Parser upgrades can discover editable table fields that did not exist when an
    in-progress report was created. The question definition and its current mapping
    are copied narrowly onto that report's version when the field is first edited.
    """

    definition = await session.scalar(
        select(QuestionDefinition).where(QuestionDefinition.key == field_name)
    )
    if definition is None:
        return None
    template_id = await session.scalar(
        select(ReportTemplateVersion.template_id).where(
            ReportTemplateVersion.id == template_version_id
        )
    )
    if template_id is None:
        return None
    current_mapping = await session.scalar(
        select(TemplateQuestion)
        .join(
            ReportTemplateVersion,
            ReportTemplateVersion.id == TemplateQuestion.template_version_id,
        )
        .where(
            ReportTemplateVersion.template_id == template_id,
            TemplateQuestion.question_id == definition.id,
            TemplateQuestion.active.is_(True),
        )
        .order_by(ReportTemplateVersion.version_number.desc())
    )
    if current_mapping is None:
        return None
    compatibility_mapping = TemplateQuestion(
        template_version_id=template_version_id,
        question_id=definition.id,
        section_id=None,
        display_order=current_mapping.display_order,
        required=False,
        default_value_json=current_mapping.default_value_json,
        conditional_rules_json=dict(current_mapping.conditional_rules_json or {}),
        mapping_rule_json={
            **dict(current_mapping.mapping_rule_json or {}),
            "compatibility_backfill": True,
        },
        active=True,
    )
    session.add(compatibility_mapping)
    await session.flush()
    return compatibility_mapping


async def _may_edit_report(
    session: AsyncSession,
    report: Report,
    actor: ApplicationUser,
) -> bool:
    if actor.id in {report.created_by_user_id, report.owner_user_id}:
        return True
    assignment = await session.scalar(
        select(ReportStageAssignment)
        .join(
            ReportApprovalStage,
            ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
        )
        .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
        .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
        .where(
            ReportRevision.report_id == report.id,
            ReportStageAssignment.user_id == actor.id,
        )
    )
    if assignment is not None:
        return True
    elevated_role = await session.scalar(
        select(Role.key)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == actor.id,
            Role.active.is_(True),
            Role.key.in_(("administrator", "qc")),
        )
    )
    if elevated_role == "administrator":
        return True
    if elevated_role != "qc":
        return False
    active_qc_stage = await session.scalar(
        select(ReportApprovalStage.id)
        .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
        .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
        .where(
            ReportRevision.report_id == report.id,
            ReportRevision.revision_number == report.current_revision_number,
            ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
            ReportApprovalStage.required_role_key_snapshot == "qc",
        )
    )
    return active_qc_stage is not None
