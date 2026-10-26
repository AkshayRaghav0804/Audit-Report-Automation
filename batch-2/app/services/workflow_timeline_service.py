"""Report-specific approval timeline for makers and reviewers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.database.models.actor import ApplicationUser
from app.database.models.approval import (
    ApprovalCycle,
    ReportApprovalStage,
    ReportStageAssignment,
)
from app.database.models.enums import ApprovalStageStatus, AssignmentStatus
from app.database.models.report import Report, ReportRevision, ReportWorkflowAssignment
from app.database.models.workflow import ApprovalStepDefinition
from app.database.session import DatabaseManager, database


@dataclass(frozen=True, slots=True)
class TimelineReviewer:
    label: str
    email: str
    status: str
    acted_at: datetime | None
    is_team: bool = False


@dataclass(frozen=True, slots=True)
class TimelineStage:
    stage_code: str
    label: str
    status: str
    reviewers: tuple[TimelineReviewer, ...]
    skip_reason: str | None = None


@dataclass(frozen=True, slots=True)
class WorkflowTimeline:
    maker_name: str
    maker_email: str
    maker_status: str
    stages: tuple[TimelineStage, ...]


class WorkflowTimelineService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def load(self, public_id: str) -> WorkflowTimeline:
        async with self.manager.session() as session:
            row = (
                await session.execute(
                    select(Report, ApplicationUser.display_name, ApplicationUser.email)
                    .join(ApplicationUser, ApplicationUser.id == Report.created_by_user_id)
                    .where(Report.public_id == public_id)
                )
            ).one_or_none()
            if row is None:
                raise ValueError("Report not found.")
            report, maker_name, maker_email = row
            revision = await session.scalar(
                select(ReportRevision).where(
                    ReportRevision.report_id == report.id,
                    ReportRevision.revision_number == report.current_revision_number,
                )
            )
            if revision is None:
                raise RuntimeError("The report has no current revision.")
            cycle = await session.scalar(
                select(ApprovalCycle)
                .where(ApprovalCycle.report_revision_id == revision.id)
                .order_by(ApprovalCycle.cycle_number.desc())
                .limit(1)
            )
            if cycle is not None:
                runtime_stages = tuple(
                    await session.scalars(
                        select(ReportApprovalStage)
                        .where(ReportApprovalStage.approval_cycle_id == cycle.id)
                        .order_by(ReportApprovalStage.sequence)
                    )
                )
                by_stage: dict[UUID, list[ReportStageAssignment]] = {}
                assignments = await session.scalars(
                    select(ReportStageAssignment)
                    .join(
                        ReportApprovalStage,
                        ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
                    )
                    .where(ReportApprovalStage.approval_cycle_id == cycle.id)
                    .order_by(ReportStageAssignment.assignment_order)
                )
                for runtime_assignment in assignments:
                    by_stage.setdefault(runtime_assignment.report_approval_stage_id, []).append(
                        runtime_assignment
                    )
                stages = tuple(
                    _runtime_stage(stage, tuple(by_stage.get(stage.id, ())))
                    for stage in runtime_stages
                )
                return WorkflowTimeline(str(maker_name), str(maker_email), "Submitted", stages)

            steps = tuple(
                await session.scalars(
                    select(ApprovalStepDefinition)
                    .where(ApprovalStepDefinition.workflow_version_id == report.workflow_version_id)
                    .order_by(ApprovalStepDefinition.sequence)
                )
            )
            draft_assignments = tuple(
                await session.scalars(
                    select(ReportWorkflowAssignment)
                    .where(ReportWorkflowAssignment.report_id == report.id)
                    .order_by(
                        ReportWorkflowAssignment.step_definition_id,
                        ReportWorkflowAssignment.assignment_order,
                    )
                )
            )
            by_step: dict[UUID, list[ReportWorkflowAssignment]] = {}
            for assignment in draft_assignments:
                by_step.setdefault(assignment.step_definition_id, []).append(assignment)
            return WorkflowTimeline(
                str(maker_name),
                str(maker_email),
                "Draft",
                tuple(_draft_stage(step, tuple(by_step.get(step.id, ()))) for step in steps),
            )


def _runtime_stage(
    stage: ReportApprovalStage,
    assignments: tuple[ReportStageAssignment, ...],
) -> TimelineStage:
    is_qc = stage.required_role_key_snapshot == "qc"
    reviewers = tuple(
        TimelineReviewer(
            label="QC Team" if is_qc else assignment.display_name_snapshot,
            email=assignment.email_snapshot,
            status=_assignment_label(assignment.status),
            acted_at=assignment.acted_at,
            is_team=is_qc,
        )
        for assignment in assignments
    )
    return TimelineStage(
        stage.stage_code,
        "QC Team Review" if is_qc else stage.display_name_snapshot,
        _stage_label(stage.status),
        reviewers,
        stage.skip_reason,
    )


def _draft_stage(
    step: ApprovalStepDefinition,
    assignments: tuple[ReportWorkflowAssignment, ...],
) -> TimelineStage:
    is_qc = step.required_role_key == "qc"
    skipped = not assignments and step.min_assignees == 0
    reviewers = tuple(
        TimelineReviewer(
            label="QC Team" if is_qc else assignment.display_name_snapshot,
            email=assignment.email_snapshot,
            status="Upcoming",
            acted_at=None,
            is_team=is_qc,
        )
        for assignment in assignments
    )
    return TimelineStage(
        step.stage_code,
        "QC Team Review" if is_qc else step.display_name,
        "Not required" if skipped else "Upcoming",
        reviewers,
        "Optional stage not selected" if skipped else None,
    )


def _assignment_label(status: AssignmentStatus) -> str:
    return {
        AssignmentStatus.PENDING: "Upcoming",
        AssignmentStatus.ACTIVE: "Awaiting decision",
        AssignmentStatus.APPROVED: "Approved",
        AssignmentStatus.RETURNED: "Returned",
        AssignmentStatus.REJECTED: "Rejected",
        AssignmentStatus.CANCELLED: "Not required",
        AssignmentStatus.REASSIGNED: "Reassigned",
    }[status]


def _stage_label(status: ApprovalStageStatus) -> str:
    return {
        ApprovalStageStatus.PENDING: "Upcoming",
        ApprovalStageStatus.ACTIVE: "Current",
        ApprovalStageStatus.APPROVED: "Approved",
        ApprovalStageStatus.RETURNED: "Returned",
        ApprovalStageStatus.REJECTED: "Rejected",
        ApprovalStageStatus.SKIPPED: "Not required",
        ApprovalStageStatus.CANCELLED: "Cancelled",
    }[status]
