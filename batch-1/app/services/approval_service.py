"""Transactional report submission and approval-stage transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.models.approval import (
    ApprovalAction,
    ApprovalCycle,
    ReportApprovalStage,
    ReportStageAssignment,
)
from app.database.models.enums import (
    AnswerRequirement,
    ApprovalActionType,
    ApprovalCycleStatus,
    ApprovalStageStatus,
    AssignmentStatus,
    CompletionPolicy,
    ExecutionMode,
    ReportStatus,
    RevisionStatus,
)
from app.database.models.mixins import utc_now
from app.database.models.report import (
    Report,
    ReportAnswer,
    ReportRevision,
    ReportSignatory,
    ReportWorkflowAssignment,
)
from app.database.models.template import QuestionDefinition, TemplateQuestion
from app.database.models.workflow import ApprovalStepDefinition
from app.database.session import DatabaseManager, database
from app.services.notification_service import (
    notify_report_completed,
    notify_report_returned,
    notify_stage_activated,
    notify_stage_approved,
)
from app.services.submission_policy import has_answer, should_validate

MAX_APPROVAL_NOTE_LENGTH = 10_000


@dataclass(frozen=True, slots=True)
class SubmissionReadiness:
    target_stage: str | None
    answer_requirement: AnswerRequirement | None
    missing_fields: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.target_stage is not None and not self.missing_fields


@dataclass(frozen=True, slots=True)
class ApprovalTransition:
    report_public_id: str
    completed_stage: str | None
    active_stage: str | None
    report_status: ReportStatus


class ApprovalService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def submission_readiness(self, public_id: str) -> SubmissionReadiness:
        async with self.manager.session() as session:
            report = await _report(session, public_id)
            revision = await _current_revision(session, report)
            steps = await _steps(session, report)
            draft_assignments = await _draft_assignments(session, report)
            resume_code = await _resume_stage_code(session, revision)
            target = _first_eligible_step(steps, draft_assignments, resume_code)
            if target is None:
                return SubmissionReadiness(None, None, ())
            missing = await _missing_fields(session, report, revision, target.answer_requirement)
            return SubmissionReadiness(target.display_name, target.answer_requirement, missing)

    async def submit(self, public_id: str, actor_email: str) -> ApprovalTransition:
        async with self.manager.session() as session:
            async with session.begin():
                actor = await _actor(session, actor_email)
                report = await _report(session, public_id)
                if actor.id not in {report.created_by_user_id, report.owner_user_id}:
                    raise PermissionError("Only the report owner can submit this report.")
                if report.status not in {ReportStatus.DRAFT, ReportStatus.RETURNED}:
                    raise ValueError("Only draft or returned reports can be submitted.")
                revision = await _current_revision(session, report)
                steps = await _steps(session, report)
                draft_assignments = await _draft_assignments(session, report)
                resume_code = await _resume_stage_code(session, revision)
                target = _first_eligible_step(steps, draft_assignments, resume_code)
                if target is None:
                    raise ValueError("The report workflow has no assigned approval stage.")
                missing = await _missing_fields(
                    session, report, revision, target.answer_requirement
                )
                if missing:
                    raise ValueError(
                        f"Complete {len(missing)} required answer(s) before submitting to "
                        f"{target.display_name}."
                    )
                cycle_number = (
                    await session.scalar(
                        select(func.max(ApprovalCycle.cycle_number)).where(
                            ApprovalCycle.report_revision_id == revision.id
                        )
                    )
                    or 0
                ) + 1
                cycle = ApprovalCycle(
                    report_revision_id=revision.id,
                    cycle_number=cycle_number,
                    status=ApprovalCycleStatus.ACTIVE,
                    submitted_by_user_id=actor.id,
                    resume_stage_code=resume_code,
                )
                session.add(cycle)
                await session.flush()
                active_stage_name: str | None = None
                active_stage_record: ReportApprovalStage | None = None
                target_reached = False
                for step in steps:
                    assignees = draft_assignments.get(step.id, ())
                    before_resume = bool(resume_code) and not target_reached
                    if resume_code == step.stage_code:
                        target_reached = True
                        before_resume = False
                    skipped = before_resume or (not assignees and step.min_assignees == 0)
                    is_active = not skipped and active_stage_name is None
                    activated_at = utc_now() if is_active else None
                    stage = ReportApprovalStage(
                        approval_cycle_id=cycle.id,
                        step_definition_id=step.id,
                        stage_code=step.stage_code,
                        display_name_snapshot=step.display_name,
                        sequence=step.sequence,
                        required_role_key_snapshot=step.required_role_key,
                        required_permission_snapshot=step.required_permission,
                        status=(
                            ApprovalStageStatus.SKIPPED
                            if skipped
                            else ApprovalStageStatus.ACTIVE
                            if is_active
                            else ApprovalStageStatus.PENDING
                        ),
                        min_assignees=step.min_assignees,
                        max_assignees=step.max_assignees,
                        execution_mode=step.execution_mode,
                        completion_policy=step.completion_policy,
                        approval_threshold=step.approval_threshold,
                        answer_requirement=step.answer_requirement,
                        skip_reason=(
                            "Completed in an earlier approval cycle"
                            if before_resume
                            else "Optional stage has no assignee"
                            if skipped
                            else None
                        ),
                        activated_at=activated_at,
                        due_at=_due_at(activated_at, step.due_in_days),
                        completed_at=utc_now() if skipped else None,
                    )
                    session.add(stage)
                    await session.flush()
                    if is_active:
                        active_stage_name = step.display_name
                        active_stage_record = stage
                    for draft_assignment in assignees:
                        session.add(
                            ReportStageAssignment(
                                report_approval_stage_id=stage.id,
                                user_id=draft_assignment.user_id,
                                assignment_order=draft_assignment.assignment_order,
                                display_name_snapshot=draft_assignment.display_name_snapshot,
                                email_snapshot=draft_assignment.email_snapshot,
                                role_key_snapshot=draft_assignment.role_key_snapshot,
                                status=_initial_assignment_status(
                                    skipped=skipped,
                                    stage_active=is_active,
                                    execution_mode=step.execution_mode,
                                    assignment_order=draft_assignment.assignment_order,
                                ),
                                assigned_by_user_id=draft_assignment.selected_by_user_id,
                            )
                        )
                activity_at = utc_now()
                previous_report_status = report.status
                report.status = ReportStatus.IN_REVIEW
                from app.services.section_access_service import expire_requests

                await expire_requests(session, report.id)
                report.submitted_at = activity_at
                report.last_activity_at = activity_at
                revision.status = RevisionStatus.SUBMITTED
                revision.submitted_at = activity_at
                session.add(
                    ApprovalAction(
                        report_id=report.id,
                        report_revision_id=revision.id,
                        approval_cycle_id=cycle.id,
                        actor_user_id=actor.id,
                        actor_role_snapshot="preparer",
                        action=ApprovalActionType.SUBMITTED,
                        previous_state=previous_report_status.value,
                        resulting_state=ReportStatus.IN_REVIEW.value,
                    )
                )
                if active_stage_record is not None:
                    active_stage_name = await _stage_destination_label(session, active_stage_record)
                    await notify_stage_activated(
                        session,
                        report,
                        active_stage_record,
                        received_from=actor.display_name,
                    )
                return ApprovalTransition(
                    report_public_id=report.public_id,
                    completed_stage=None,
                    active_stage=active_stage_name,
                    report_status=report.status,
                )

    async def approve(
        self, public_id: str, actor_email: str, notes: str | None = None
    ) -> ApprovalTransition:
        normalized_notes = (notes or "").strip()
        if len(normalized_notes) > MAX_APPROVAL_NOTE_LENGTH:
            raise ValueError("Approval notes cannot exceed 10,000 characters.")
        async with self.manager.session() as session:
            async with session.begin():
                actor = await _actor(session, actor_email)
                report = await _report(session, public_id)
                revision = await _current_revision(session, report)
                cycle = await _active_cycle(session, revision)
                stage = await _active_stage(session, cycle)
                assignment = await _assignment_for_actor(session, stage, actor)
                if assignment is None:
                    raise PermissionError("This approval stage is not assigned to you.")
                activity_at = utc_now()
                assignment.status = AssignmentStatus.APPROVED
                assignment.acted_at = activity_at
                report.last_activity_at = activity_at
                assignments = tuple(
                    await session.scalars(
                        select(ReportStageAssignment).where(
                            ReportStageAssignment.report_approval_stage_id == stage.id
                        )
                    )
                )
                stage_completed = _stage_is_complete(stage, assignments)
                active_stage_name: str | None = stage.display_name_snapshot
                next_stage_to_notify: ReportApprovalStage | None = None
                completed_report = False
                if stage_completed:
                    stage.status = ApprovalStageStatus.APPROVED
                    stage.completed_at = activity_at
                    next_stage = await _next_pending_stage(session, cycle, stage.sequence)
                    if next_stage is not None:
                        if next_stage.answer_requirement is AnswerRequirement.ALL_APPLICABLE:
                            missing = await _missing_fields(
                                session,
                                report,
                                revision,
                                AnswerRequirement.ALL_APPLICABLE,
                            )
                            if missing:
                                raise ValueError(
                                    f"Complete all {len(missing)} remaining report answer(s) "
                                    f"before moving to {next_stage.display_name_snapshot}."
                                )
                        await _activate_stage(session, next_stage)
                        active_stage_name = await _stage_destination_label(session, next_stage)
                        next_stage_to_notify = next_stage
                    else:
                        cycle.status = ApprovalCycleStatus.APPROVED
                        cycle.completed_at = activity_at
                        report.status = ReportStatus.APPROVED
                        report.approved_at = activity_at
                        revision.status = RevisionStatus.FINALIZED
                        revision.finalized_at = activity_at
                        active_stage_name = None
                        completed_report = True
                elif stage.execution_mode is ExecutionMode.SEQUENTIAL:
                    next_assignment = await session.scalar(
                        select(ReportStageAssignment)
                        .where(
                            ReportStageAssignment.report_approval_stage_id == stage.id,
                            ReportStageAssignment.status == AssignmentStatus.PENDING,
                        )
                        .order_by(ReportStageAssignment.assignment_order)
                    )
                    if next_assignment is not None:
                        next_assignment.status = AssignmentStatus.ACTIVE
                        await session.flush()
                        active_stage_name = (
                            f"{stage.display_name_snapshot} — "
                            f"{next_assignment.display_name_snapshot}"
                        )
                        next_stage_to_notify = stage
                session.add(
                    ApprovalAction(
                        report_id=report.id,
                        report_revision_id=revision.id,
                        approval_cycle_id=cycle.id,
                        report_approval_stage_id=stage.id,
                        assignment_id=assignment.id,
                        actor_user_id=actor.id,
                        actor_role_snapshot=assignment.role_key_snapshot,
                        action=ApprovalActionType.APPROVED,
                        notes=normalized_notes or None,
                        previous_state=ApprovalStageStatus.ACTIVE.value,
                        resulting_state=stage.status.value,
                    )
                )
                actor_label = (
                    "QC Team" if stage.required_role_key_snapshot == "qc" else actor.display_name
                )
                await notify_stage_approved(
                    session,
                    report,
                    stage,
                    actor_label=actor_label,
                )
                if next_stage_to_notify is not None:
                    await notify_stage_activated(
                        session,
                        report,
                        next_stage_to_notify,
                        received_from=actor_label,
                    )
                if completed_report:
                    await notify_report_completed(session, report)
                return ApprovalTransition(
                    report_public_id=report.public_id,
                    completed_stage=stage.display_name_snapshot if stage_completed else None,
                    active_stage=active_stage_name,
                    report_status=report.status,
                )

    async def return_for_changes(
        self,
        public_id: str,
        actor_email: str,
        notes: str,
        restart_stage_code: str | None = None,
    ) -> ApprovalTransition:
        if not notes.strip():
            raise ValueError("A return reason is required.")
        if len(notes.strip()) > MAX_APPROVAL_NOTE_LENGTH:
            raise ValueError("A return reason cannot exceed 10,000 characters.")
        async with self.manager.session() as session:
            async with session.begin():
                actor = await _actor(session, actor_email)
                report = await _report(session, public_id)
                revision = await _current_revision(session, report)
                cycle = await _active_cycle(session, revision)
                stage = await _active_stage(session, cycle)
                assignment = await _assignment_for_actor(session, stage, actor)
                if assignment is None:
                    raise PermissionError("This approval stage is not assigned to you.")
                if restart_stage_code is not None:
                    restart_stage = await session.scalar(
                        select(ApprovalStepDefinition).where(
                            ApprovalStepDefinition.workflow_version_id
                            == report.workflow_version_id,
                            ApprovalStepDefinition.stage_code == restart_stage_code,
                            ApprovalStepDefinition.sequence <= stage.sequence,
                        )
                    )
                    if restart_stage is None:
                        raise ValueError("Select a valid earlier stage to restart approval.")
                activity_at = utc_now()
                assignment.status = AssignmentStatus.RETURNED
                assignment.acted_at = activity_at
                stage.status = ApprovalStageStatus.RETURNED
                stage.completed_at = activity_at
                cycle.status = ApprovalCycleStatus.RETURNED
                cycle.completed_at = activity_at
                cycle.resume_stage_code = restart_stage_code or stage.stage_code
                report.status = ReportStatus.RETURNED
                report.last_activity_at = activity_at
                revision.status = RevisionStatus.EDITABLE
                session.add(
                    ApprovalAction(
                        report_id=report.id,
                        report_revision_id=revision.id,
                        approval_cycle_id=cycle.id,
                        report_approval_stage_id=stage.id,
                        assignment_id=assignment.id,
                        actor_user_id=actor.id,
                        actor_role_snapshot=assignment.role_key_snapshot,
                        action=ApprovalActionType.RETURNED,
                        notes=notes.strip(),
                        previous_state=ApprovalStageStatus.ACTIVE.value,
                        resulting_state=ApprovalStageStatus.RETURNED.value,
                    )
                )
                await notify_report_returned(
                    session,
                    report,
                    stage,
                    actor_label=(
                        "QC Team"
                        if stage.required_role_key_snapshot == "qc"
                        else actor.display_name
                    ),
                    reason=notes.strip(),
                )
                return ApprovalTransition(
                    report_public_id=report.public_id,
                    completed_stage=stage.display_name_snapshot,
                    active_stage=None,
                    report_status=report.status,
                )


async def _actor(session: AsyncSession, email: str) -> ApplicationUser:
    actor = await session.scalar(
        select(ApplicationUser).where(
            ApplicationUser.email == email.casefold(), ApplicationUser.active.is_(True)
        )
    )
    if actor is None:
        raise PermissionError("The current user is not active in the people directory.")
    return actor


async def _actor_has_role(session: AsyncSession, user_id: UUID, role_key: str) -> bool:
    return bool(
        await session.scalar(
            select(UserRole.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                UserRole.user_id == user_id,
                Role.key == role_key,
                Role.active.is_(True),
            )
        )
    )


async def _assignment_for_actor(
    session: AsyncSession,
    stage: ReportApprovalStage,
    actor: ApplicationUser,
) -> ReportStageAssignment | None:
    direct = await session.scalar(
        select(ReportStageAssignment).where(
            ReportStageAssignment.report_approval_stage_id == stage.id,
            ReportStageAssignment.user_id == actor.id,
            ReportStageAssignment.status == AssignmentStatus.ACTIVE,
        )
    )
    if direct is not None:
        return direct
    if stage.required_role_key_snapshot != "qc":
        return None
    if not await _actor_has_role(session, actor.id, "qc"):
        return None
    return cast(
        ReportStageAssignment | None,
        await session.scalar(
            select(ReportStageAssignment)
            .where(
                ReportStageAssignment.report_approval_stage_id == stage.id,
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
            )
            .order_by(ReportStageAssignment.assignment_order)
        ),
    )


async def _report(session: AsyncSession, public_id: str) -> Report:
    report = await session.scalar(
        select(Report).where(Report.public_id == public_id).with_for_update()
    )
    if report is None:
        raise ValueError("Report not found.")
    return report


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


async def _steps(session: AsyncSession, report: Report) -> tuple[ApprovalStepDefinition, ...]:
    return tuple(
        await session.scalars(
            select(ApprovalStepDefinition)
            .where(ApprovalStepDefinition.workflow_version_id == report.workflow_version_id)
            .order_by(ApprovalStepDefinition.sequence)
        )
    )


async def _draft_assignments(
    session: AsyncSession, report: Report
) -> dict[UUID, tuple[ReportWorkflowAssignment, ...]]:
    rows = tuple(
        await session.scalars(
            select(ReportWorkflowAssignment)
            .where(ReportWorkflowAssignment.report_id == report.id)
            .order_by(ReportWorkflowAssignment.assignment_order)
        )
    )
    grouped: dict[UUID, list[ReportWorkflowAssignment]] = {}
    for row in rows:
        grouped.setdefault(row.step_definition_id, []).append(row)
    return {key: tuple(value) for key, value in grouped.items()}


async def _resume_stage_code(session: AsyncSession, revision: ReportRevision) -> str | None:
    cycle = await session.scalar(
        select(ApprovalCycle)
        .where(
            ApprovalCycle.report_revision_id == revision.id,
            ApprovalCycle.status == ApprovalCycleStatus.RETURNED,
        )
        .order_by(ApprovalCycle.cycle_number.desc())
    )
    return cycle.resume_stage_code if cycle else None


def _first_eligible_step(
    steps: tuple[ApprovalStepDefinition, ...],
    assignments: dict[UUID, tuple[ReportWorkflowAssignment, ...]],
    resume_code: str | None,
) -> ApprovalStepDefinition | None:
    resume_reached = resume_code is None
    for step in steps:
        if step.stage_code == resume_code:
            resume_reached = True
        if resume_reached and (assignments.get(step.id) or step.min_assignees > 0):
            return step
    return None


async def _missing_fields(
    session: AsyncSession,
    report: Report,
    revision: ReportRevision,
    requirement: AnswerRequirement,
) -> tuple[str, ...]:
    statement = (
        select(TemplateQuestion, QuestionDefinition)
        .join(QuestionDefinition, QuestionDefinition.id == TemplateQuestion.question_id)
        .where(
            TemplateQuestion.template_version_id == report.template_version_id,
            TemplateQuestion.active.is_(True),
        )
        .order_by(TemplateQuestion.display_order)
    )
    questions = tuple(await session.execute(statement))
    stored_answers = {
        answer.template_question_id: answer.value_json
        for answer in await session.scalars(
            select(ReportAnswer).where(ReportAnswer.report_revision_id == revision.id)
        )
    }
    answers = {
        definition.key: stored_answers.get(
            template_question.id, template_question.default_value_json
        )
        for template_question, definition in questions
    }
    signatory = await session.scalar(
        select(ReportSignatory)
        .where(ReportSignatory.report_id == report.id)
        .order_by(ReportSignatory.created_at)
    )
    if signatory is not None:
        answers["partner_name"] = signatory.display_name_snapshot
        answers["membership_number"] = signatory.membership_number_snapshot
    missing_answers = tuple(
        definition.key
        for template_question, definition in questions
        if should_validate(
            definition.key,
            source_required=template_question.required,
            requirement=requirement,
            answers=answers,
        )
        and not has_answer(answers.get(definition.key))
    )
    return missing_answers


async def _active_cycle(session: AsyncSession, revision: ReportRevision) -> ApprovalCycle:
    cycle = await session.scalar(
        select(ApprovalCycle).where(
            ApprovalCycle.report_revision_id == revision.id,
            ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
        )
    )
    if cycle is None:
        raise ValueError("The report has no active approval cycle.")
    return cycle


async def _active_stage(session: AsyncSession, cycle: ApprovalCycle) -> ReportApprovalStage:
    stage = await session.scalar(
        select(ReportApprovalStage).where(
            ReportApprovalStage.approval_cycle_id == cycle.id,
            ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
        )
    )
    if stage is None:
        raise ValueError("The approval cycle has no active stage.")
    return stage


def _stage_is_complete(
    stage: ReportApprovalStage, assignments: tuple[ReportStageAssignment, ...]
) -> bool:
    approved = sum(item.status is AssignmentStatus.APPROVED for item in assignments)
    if stage.completion_policy is CompletionPolicy.ANY:
        return approved >= 1
    if stage.completion_policy is CompletionPolicy.THRESHOLD:
        return approved >= (stage.approval_threshold or 1)
    return approved == len(assignments)


async def _next_pending_stage(
    session: AsyncSession, cycle: ApprovalCycle, after_sequence: int
) -> ReportApprovalStage | None:
    return cast(
        ReportApprovalStage | None,
        await session.scalar(
            select(ReportApprovalStage)
            .where(
                ReportApprovalStage.approval_cycle_id == cycle.id,
                ReportApprovalStage.sequence > after_sequence,
                ReportApprovalStage.status == ApprovalStageStatus.PENDING,
            )
            .order_by(ReportApprovalStage.sequence)
        ),
    )


async def _activate_stage(session: AsyncSession, stage: ReportApprovalStage) -> None:
    activated_at = utc_now()
    due_in_days = await session.scalar(
        select(ApprovalStepDefinition.due_in_days).where(
            ApprovalStepDefinition.id == stage.step_definition_id
        )
    )
    stage.status = ApprovalStageStatus.ACTIVE
    stage.activated_at = activated_at
    stage.due_at = _due_at(activated_at, due_in_days)
    assignments = tuple(
        await session.scalars(
            select(ReportStageAssignment)
            .where(
                ReportStageAssignment.report_approval_stage_id == stage.id,
                ReportStageAssignment.status == AssignmentStatus.PENDING,
            )
            .order_by(ReportStageAssignment.assignment_order)
        )
    )
    for index, assignment in enumerate(assignments):
        if stage.execution_mode is ExecutionMode.PARALLEL or index == 0:
            assignment.status = AssignmentStatus.ACTIVE
    await session.flush()


def _initial_assignment_status(
    *,
    skipped: bool,
    stage_active: bool,
    execution_mode: ExecutionMode,
    assignment_order: int,
) -> AssignmentStatus:
    if skipped:
        return AssignmentStatus.CANCELLED
    if stage_active and (execution_mode is ExecutionMode.PARALLEL or assignment_order == 1):
        return AssignmentStatus.ACTIVE
    return AssignmentStatus.PENDING


async def _stage_destination_label(session: AsyncSession, stage: ReportApprovalStage) -> str:
    assignment = await session.scalar(
        select(ReportStageAssignment)
        .where(
            ReportStageAssignment.report_approval_stage_id == stage.id,
            ReportStageAssignment.status == AssignmentStatus.ACTIVE,
        )
        .order_by(ReportStageAssignment.assignment_order)
    )
    if stage.required_role_key_snapshot == "qc":
        return "QC Team"
    if assignment is None:
        return stage.display_name_snapshot
    return f"{stage.display_name_snapshot} — {assignment.display_name_snapshot}"


def _due_at(activated_at: datetime | None, due_in_days: int | None) -> datetime | None:
    if activated_at is None or due_in_days is None:
        return None
    return activated_at + timedelta(days=due_in_days)
