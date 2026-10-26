"""Resolve the workflow assignee represented by the local demo role switcher."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import aliased

from app.data.mock_data import MOCK_CURRENT_USER
from app.database.models.actor import ApplicationUser
from app.database.models.approval import (
    ApprovalCycle,
    ReportApprovalStage,
    ReportStageAssignment,
)
from app.database.models.enums import (
    ApprovalCycleStatus,
    ApprovalStageStatus,
    AssignmentStatus,
    StageType,
)
from app.database.models.report import Report, ReportRevision
from app.database.models.workflow import ApprovalStepDefinition
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.demo_role_service import permissions_for_demo_role

ROLE_STAGE_TYPES = {
    "Manager": StageType.MANAGER,
    "Director": StageType.DIRECTOR,
    "Partner": StageType.PARTNER,
    "QC Reviewer": StageType.QC,
    "EQCR Reviewer": StageType.EQCR,
}


class DemoRoleIdentityService:
    """Find the latest active assignee for reports created by the demo maker."""

    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def identity_for(
        self,
        role: str,
        owner_email: str = MOCK_CURRENT_USER.email,
    ) -> User | None:
        stage_type = ROLE_STAGE_TYPES.get(role)
        if stage_type is None:
            return None
        async with self.manager.session() as session:
            report_owner = aliased(ApplicationUser)
            row = (
                await session.execute(
                    select(ApplicationUser)
                    .join(
                        ReportStageAssignment,
                        ReportStageAssignment.user_id == ApplicationUser.id,
                    )
                    .join(
                        ReportApprovalStage,
                        ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
                    )
                    .join(
                        ApprovalStepDefinition,
                        ApprovalStepDefinition.id == ReportApprovalStage.step_definition_id,
                    )
                    .join(
                        ApprovalCycle,
                        ApprovalCycle.id == ReportApprovalStage.approval_cycle_id,
                    )
                    .join(
                        ReportRevision,
                        ReportRevision.id == ApprovalCycle.report_revision_id,
                    )
                    .join(Report, Report.id == ReportRevision.report_id)
                    .join(
                        report_owner,
                        or_(
                            Report.created_by_user_id == report_owner.id,
                            Report.owner_user_id == report_owner.id,
                        ),
                    )
                    .where(
                        ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
                        ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
                        ReportStageAssignment.status == AssignmentStatus.ACTIVE,
                        ApprovalStepDefinition.stage_type == stage_type,
                        report_owner.email == owner_email.casefold(),
                    )
                    .order_by(
                        Report.last_activity_at.desc(),
                        ReportStageAssignment.assignment_order,
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
        if row is None:
            return None
        return User(
            id=str(row.id),
            name=row.display_name,
            email=row.email,
            role=role,
            permissions=permissions_for_demo_role(role),
        )
