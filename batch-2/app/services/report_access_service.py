"""Assignment-scoped report authorization and reviewer context."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.models.approval import (
    ApprovalAction,
    ApprovalCycle,
    ReportApprovalStage,
    ReportStageAssignment,
)
from app.database.models.enums import (
    ApprovalActionType,
    ApprovalCycleStatus,
    ApprovalStageStatus,
    AssignmentStatus,
    ReportStatus,
)
from app.database.models.report import Report, ReportRevision, ReportWorkflowAssignment
from app.database.models.template import ReportTemplate, ReportTemplateVersion
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.report_edit_role_service import report_edit_role


@dataclass(frozen=True, slots=True)
class ReportAccess:
    report_public_id: str
    template_key: str
    report_status: ReportStatus
    creator_name: str
    active_stage: str | None
    active_stage_id: UUID | None
    received_from: str
    is_owner: bool
    can_view: bool
    can_edit_answers: bool
    can_approve: bool
    can_return: bool
    can_comment: bool
    can_edit_fixed_sections: bool
    is_completed_review: bool
    edit_role: str = "Employee"


class ReportAccessService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def access_for(self, public_id: str, profile: User) -> ReportAccess:
        async with self.manager.session() as session:
            creator = ApplicationUser
            row = (
                await session.execute(
                    select(Report, ReportTemplate.key, creator.display_name)
                    .join(
                        ReportTemplateVersion,
                        ReportTemplateVersion.id == Report.template_version_id,
                    )
                    .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
                    .join(creator, creator.id == Report.created_by_user_id)
                    .where(Report.public_id == public_id)
                )
            ).one_or_none()
            if row is None:
                raise ValueError("Report not found.")
            report, template_key, creator_name = row
            actor = await session.scalar(
                select(ApplicationUser).where(
                    ApplicationUser.email == profile.email.casefold(),
                    ApplicationUser.active.is_(True),
                )
            )
            if actor is None:
                raise PermissionError("The current user is not active in the people directory.")

            stage = await session.scalar(
                select(ReportApprovalStage)
                .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
                .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
                .where(
                    ReportRevision.report_id == report.id,
                    ReportRevision.revision_number == report.current_revision_number,
                    ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
                    ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
                )
            )
            active_assignment = None
            if stage is not None:
                active_assignment = await session.scalar(
                    select(ReportStageAssignment).where(
                        ReportStageAssignment.report_approval_stage_id == stage.id,
                        ReportStageAssignment.user_id == actor.id,
                        ReportStageAssignment.status == AssignmentStatus.ACTIVE,
                    )
                )
                if (
                    active_assignment is None
                    and stage.required_role_key_snapshot == "qc"
                    and await _actor_has_role(session, actor.id, "qc")
                ):
                    active_assignment = await session.scalar(
                        select(ReportStageAssignment)
                        .where(
                            ReportStageAssignment.report_approval_stage_id == stage.id,
                            ReportStageAssignment.status == AssignmentStatus.ACTIVE,
                        )
                        .order_by(ReportStageAssignment.assignment_order)
                    )
            history_assignment = await session.scalar(
                select(ReportStageAssignment)
                .join(
                    ReportApprovalStage,
                    ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
                )
                .join(
                    ApprovalCycle,
                    ApprovalCycle.id == ReportApprovalStage.approval_cycle_id,
                )
                .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
                .where(
                    ReportRevision.report_id == report.id,
                    ReportStageAssignment.user_id == actor.id,
                    ReportStageAssignment.status.in_(
                        (AssignmentStatus.APPROVED, AssignmentStatus.RETURNED)
                    ),
                )
                .order_by(ReportStageAssignment.acted_at.desc())
            )
            if history_assignment is None and await _actor_has_role(session, actor.id, "qc"):
                history_assignment = await session.scalar(
                    select(ReportStageAssignment)
                    .join(
                        ReportApprovalStage,
                        ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
                    )
                    .join(
                        ApprovalCycle,
                        ApprovalCycle.id == ReportApprovalStage.approval_cycle_id,
                    )
                    .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
                    .where(
                        ReportRevision.report_id == report.id,
                        ReportApprovalStage.required_role_key_snapshot == "qc",
                        ReportStageAssignment.status.in_(
                            (AssignmentStatus.APPROVED, AssignmentStatus.RETURNED)
                        ),
                    )
                    .order_by(ReportStageAssignment.acted_at.desc())
                )
            is_owner = actor.id in {report.created_by_user_id, report.owner_user_id}
            is_admin = await _actor_has_role(session, actor.id, "administrator")
            active = active_assignment is not None and stage is not None
            completed = history_assignment is not None and not active
            permission_approver = await session.scalar(
                select(ReportWorkflowAssignment.id).where(
                    ReportWorkflowAssignment.report_id == report.id,
                    ReportWorkflowAssignment.user_id == actor.id,
                    ReportWorkflowAssignment.role_key_snapshot.in_(
                        ("director", "director_level", "partner")
                    ),
                )
            )
            can_view = is_admin or is_owner or active or completed
            can_act = active
            report_is_editable = report.status in {
                ReportStatus.DRAFT,
                ReportStatus.RETURNED,
                ReportStatus.IN_REVIEW,
            }
            return ReportAccess(
                report_public_id=report.public_id,
                template_key=str(template_key),
                report_status=report.status,
                creator_name=str(creator_name),
                active_stage=stage.display_name_snapshot if stage is not None else None,
                active_stage_id=stage.id if stage is not None else None,
                received_from=await _received_from(session, report.id, str(creator_name)),
                is_owner=is_owner,
                can_view=can_view or permission_approver is not None,
                can_edit_answers=can_view and report_is_editable,
                can_approve=can_act,
                can_return=can_act,
                can_comment=can_view and (can_act or is_owner),
                can_edit_fixed_sections=can_view and report_is_editable,
                is_completed_review=completed,
                edit_role=await report_edit_role(session, report, actor.id),
            )


async def _received_from(session: object, report_id: UUID, creator_name: str) -> str:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    row = (
        await session.execute(
            select(ApplicationUser.display_name, ApprovalAction.actor_role_snapshot)
            .join(ApprovalAction, ApprovalAction.actor_user_id == ApplicationUser.id)
            .where(
                ApprovalAction.report_id == report_id,
                ApprovalAction.action == ApprovalActionType.APPROVED,
            )
            .order_by(ApprovalAction.action_at.desc())
            .limit(1)
        )
    ).one_or_none()
    if row is not None:
        actor_name, actor_role = row
        return "QC Team" if actor_role == "qc" else str(actor_name)
    return creator_name


async def _actor_has_role(session: object, user_id: UUID, role_key: str) -> bool:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
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
