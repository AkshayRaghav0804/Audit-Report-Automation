"""Resolve edit attribution from persisted report assignments, never caller labels."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import Role, UserRole
from app.database.models.approval import ApprovalCycle, ReportApprovalStage, ReportStageAssignment
from app.database.models.enums import ApprovalCycleStatus, ApprovalStageStatus, AssignmentStatus
from app.database.models.report import Report, ReportRevision, ReportWorkflowAssignment

ROLE_LABELS = {
    "employee": "Employee",
    "manager": "Manager",
    "director": "Director",
    "director_level": "Director",
    "manager_level": "Manager",
    "partner": "Partner",
    "qc": "QC Reviewer",
    "eqcr": "EQCR Reviewer",
    "administrator": "Administrator",
}


async def report_edit_role(session: AsyncSession, report: Report, actor_id: UUID) -> str:
    if actor_id in {report.created_by_user_id, report.owner_user_id}:
        return "Employee"
    roles = set(
        await session.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == actor_id, Role.active.is_(True))
        )
    )
    stages = (
        select(ReportApprovalStage)
        .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
        .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
        .where(
            ReportRevision.report_id == report.id,
            ReportRevision.revision_number == report.current_revision_number,
            ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
        )
    )
    active_stage = await session.scalar(
        stages.where(ReportApprovalStage.status == ApprovalStageStatus.ACTIVE)
    )
    if active_stage is not None:
        assigned = await session.scalar(
            select(ReportStageAssignment.id).where(
                ReportStageAssignment.report_approval_stage_id == active_stage.id,
                ReportStageAssignment.user_id == actor_id,
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
            )
        )
        if assigned is not None or (
            active_stage.required_role_key_snapshot == "qc" and "qc" in roles
        ):
            return ROLE_LABELS.get(active_stage.required_role_key_snapshot, "Contributor")
    assigned_role = await session.scalar(
        select(ReportWorkflowAssignment.role_key_snapshot)
        .where(
            ReportWorkflowAssignment.report_id == report.id,
            ReportWorkflowAssignment.user_id == actor_id,
        )
        .order_by(ReportWorkflowAssignment.assignment_order, ReportWorkflowAssignment.id)
    )
    if assigned_role is not None:
        return ROLE_LABELS.get(assigned_role, "Contributor")
    if "administrator" in roles:
        return "Administrator"
    if "qc" in roles:
        return "QC Reviewer"
    return "Contributor"
