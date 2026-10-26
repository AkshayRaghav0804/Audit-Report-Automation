"""Database-backed dashboard aggregates and work queues."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

from sqlalchemy import ColumnElement, and_, exists, false, func, or_, select, true
from sqlalchemy.orm import aliased

from app.config import Settings
from app.database.models.actor import ApplicationUser
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
    PublicationStatus,
    ReportStatus,
)
from app.database.models.report import (
    Report as DatabaseReport,
)
from app.database.models.report import ReportRevision
from app.database.models.template import ReportTemplate, ReportTemplateVersion
from app.database.models.workflow import ApprovalStepDefinition, ApprovalWorkflowVersion
from app.database.session import DatabaseManager, database
from app.models.report import (
    AttentionItem,
    DashboardMetric,
    DashboardSnapshot,
    Report,
    ReportPage,
    WorkflowStage,
)
from app.models.user import User
from app.services.authorization_service import refreshed_profile
from app.services.directory_service import DirectoryService
from app.services.permission_service import is_report_creator, is_report_reviewer

OPEN_STATUSES = (ReportStatus.DRAFT, ReportStatus.RETURNED, ReportStatus.IN_REVIEW)
COMPLETED_STATUSES = (ReportStatus.APPROVED, ReportStatus.FINALIZED)
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")
REPORT_VIEWS = {
    "all": "All reports",
    "active": "Active Reports",
    "my-active": "My Active Reports",
    "drafts": "My Drafts",
    "drafts-to-complete": "Drafts to Complete",
    "pending": "Pending My Approval",
    "overdue": "Overdue Approvals",
    "returned": "Returned for Changes",
    "awaiting": "Awaiting Approval",
    "in-review": "Reports in Review",
    "approved-month": "Approved This Month",
    "completed-reviews": "Completed Reviews",
    "returned-by-me": "Returned by Me",
}


async def _authoritative_profile(profile: User, manager: DatabaseManager) -> User:
    if Settings.from_environment().demo_auth_enabled:
        return profile
    current = await refreshed_profile(profile, manager)
    if current is None:
        raise PermissionError("Your application access has been revoked.")
    return current


def report_view_key(value: str | None) -> str:
    if not value:
        return "all"
    if value in REPORT_VIEWS:
        return value
    for key, label in REPORT_VIEWS.items():
        if value == label:
            return key
    raise ValueError("Unknown report view. Clear the filters and try again.")


class DashboardService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def list_reports(
        self,
        profile: User,
        *,
        view: str | None = None,
        stage: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> ReportPage:
        profile = await _authoritative_profile(profile, self.manager)
        view_key = report_view_key(view)
        if page_size not in {10, 25, 50}:
            raise ValueError("Choose 10, 25 or 50 reports per page.")
        directory = DirectoryService(self.manager)
        person = await directory.find_person_by_email(profile.email)
        if person is None or not person.active:
            raise PermissionError("Your active people-directory account could not be found.")
        async with self.manager.session() as session:
            visible = _visibility_filter(profile, person.id, "qc" in person.roles)
            stages = await _workflow_stages(session, visible)
            stage_names = tuple(item.name for item in stages)
            if stage and stage not in stage_names:
                raise ValueError("Unknown workflow stage. Clear the filters and try again.")
            filters = [
                visible,
                _report_view_filter(profile, person.id, "qc" in person.roles, view_key),
            ]
            if stage:
                filters.append(_workflow_filter(stage))
            if search and search.strip():
                term = search.strip()
                filters.append(
                    or_(
                        DatabaseReport.public_id.icontains(term, autoescape=True),
                        DatabaseReport.title.icontains(term, autoescape=True),
                        DatabaseReport.entity_name.icontains(term, autoescape=True),
                        DatabaseReport.engagement_code.icontains(term, autoescape=True),
                    )
                )
            filtered = and_(*filters)
            total = int(
                await session.scalar(select(func.count(DatabaseReport.id)).where(filtered)) or 0
            )
            page = max(1, min(page, max(1, (total + page_size - 1) // page_size)))
            reports = await _recent_reports(
                session, filtered, page_size, offset=(page - 1) * page_size
            )
        return ReportPage(reports, total, page, page_size, stage_names)

    async def load(
        self,
        profile: User,
        *,
        recent_limit: int = 5,
        attention_limit: int = 10,
    ) -> DashboardSnapshot:
        profile = await _authoritative_profile(profile, self.manager)
        directory = DirectoryService(self.manager)
        person = await directory.find_person_by_email(profile.email)
        if person is None and Settings.from_environment().demo_auth_enabled:
            person = await directory.ensure_profile_person(profile)
        if person is None or not person.active:
            raise PermissionError("Your active people-directory account could not be found.")

        generated_at = datetime.now(UTC)
        async with self.manager.session() as session:
            is_qc_member = "qc" in person.roles
            visible = _visibility_filter(profile, person.id, is_qc_member)
            status_counts = {
                status: int(count)
                for status, count in await session.execute(
                    select(DatabaseReport.status, func.count(DatabaseReport.id))
                    .where(visible)
                    .group_by(DatabaseReport.status)
                )
            }
            owner_counts = {
                status: int(count)
                for status, count in await session.execute(
                    select(DatabaseReport.status, func.count(DatabaseReport.id))
                    .where(
                        DatabaseReport.owner_user_id == person.id,
                        DatabaseReport.status.in_(OPEN_STATUSES),
                    )
                    .group_by(DatabaseReport.status)
                )
            }
            pending_count = (
                await _pending_approval_count(session, person.id, is_qc_member)
                if "report.approve" in profile.permissions
                else 0
            )
            overdue_count = (
                await _overdue_approval_count(session, person.id, is_qc_member, generated_at)
                if "report.approve" in profile.permissions
                else 0
            )
            review_activity = await _review_activity_counts(session, person.id, is_qc_member)
            approved_month_count = await session.scalar(
                select(func.count(DatabaseReport.id)).where(
                    visible,
                    DatabaseReport.status.in_(COMPLETED_STATUSES),
                    DatabaseReport.approved_at >= _month_start_utc(generated_at),
                    DatabaseReport.approved_at < _next_month_start_utc(generated_at),
                )
            )

            metrics = _metrics(
                profile,
                status_counts,
                owner_counts,
                int(pending_count or 0),
                int(overdue_count or 0),
                int(approved_month_count or 0),
                review_activity,
                generated_at,
            )
            attention_items = await _attention_items(
                session,
                person.id,
                profile,
                is_qc_member,
                generated_at,
                attention_limit,
            )
            recent_reports = await _recent_reports(
                session,
                visible,
                recent_limit,
            )
            workflow_stages = await _workflow_stages(session, visible, status_counts=status_counts)
        return DashboardSnapshot(
            metrics=metrics,
            attention_items=attention_items,
            recent_reports=recent_reports,
            workflow_stages=workflow_stages,
            generated_at=generated_at,
        )


def unavailable_dashboard_snapshot() -> DashboardSnapshot:
    return DashboardSnapshot(
        metrics=(
            DashboardMetric("Active Reports", None, "Data unavailable", "description", "blue"),
            DashboardMetric("Drafts to Complete", None, "Data unavailable", "edit_note", "amber"),
            DashboardMetric("Pending My Approval", None, "Data unavailable", "approval", "amber"),
            DashboardMetric("Reports in Review", None, "Data unavailable", "rate_review", "teal"),
            DashboardMetric("Approved This Month", None, "Data unavailable", "task_alt", "green"),
        ),
        attention_items=(),
        recent_reports=(),
        workflow_stages=(),
        generated_at=datetime.now(UTC),
    )


def _assignment_filter(
    user_id: UUID,
    is_qc_member: bool,
    status: AssignmentStatus,
    *,
    overdue: bool = False,
) -> ColumnElement[bool]:
    statement = (
        select(ReportStageAssignment.id)
        .join(
            ReportApprovalStage,
            ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
        )
        .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
        .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
        .where(
            ReportRevision.report_id == DatabaseReport.id,
            or_(
                ReportStageAssignment.user_id == user_id,
                and_(
                    true() if is_qc_member else false(),
                    ReportStageAssignment.role_key_snapshot == "qc",
                ),
            ),
            ReportStageAssignment.status == status,
        )
    )
    if status == AssignmentStatus.ACTIVE:
        statement = statement.where(
            DatabaseReport.status == ReportStatus.IN_REVIEW,
            ReportRevision.revision_number == DatabaseReport.current_revision_number,
            ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
        )
    if overdue:
        statement = statement.where(ReportApprovalStage.due_at < datetime.now(UTC))
    return exists(statement)


def _workflow_filter(stage: str) -> ColumnElement[bool]:
    if stage == "Draft":
        return DatabaseReport.status == ReportStatus.DRAFT
    if stage == "Returned":
        return DatabaseReport.status == ReportStatus.RETURNED
    if stage == "Approved":
        return DatabaseReport.status.in_(COMPLETED_STATUSES)
    return exists(
        select(ReportApprovalStage.id)
        .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
        .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
        .where(
            ReportRevision.report_id == DatabaseReport.id,
            ReportRevision.revision_number == DatabaseReport.current_revision_number,
            DatabaseReport.status == ReportStatus.IN_REVIEW,
            ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
            ReportApprovalStage.display_name_snapshot == stage,
        )
    )


def _report_view_filter(
    profile: User, user_id: UUID, is_qc_member: bool, view: str
) -> ColumnElement[bool]:
    owned = DatabaseReport.owner_user_id == user_id
    if view == "all":
        return true()
    if view == "active":
        return DatabaseReport.status.in_(OPEN_STATUSES)
    if view == "my-active":
        return and_(owned, DatabaseReport.status.in_(OPEN_STATUSES))
    if view == "drafts":
        return and_(owned, DatabaseReport.status == ReportStatus.DRAFT)
    if view == "drafts-to-complete":
        return and_(owned, DatabaseReport.status.in_((ReportStatus.DRAFT, ReportStatus.RETURNED)))
    if view == "returned":
        return and_(owned, DatabaseReport.status == ReportStatus.RETURNED)
    if view == "awaiting":
        return and_(owned, DatabaseReport.status == ReportStatus.IN_REVIEW)
    if view == "in-review":
        return DatabaseReport.status == ReportStatus.IN_REVIEW
    if view == "approved-month":
        now = datetime.now(UTC)
        return and_(
            DatabaseReport.status.in_(COMPLETED_STATUSES),
            DatabaseReport.approved_at >= _month_start_utc(now),
            DatabaseReport.approved_at < _next_month_start_utc(now),
        )
    if view in {"pending", "overdue"}:
        if "report.approve" not in profile.permissions:
            return false()
        return _assignment_filter(
            user_id, is_qc_member, AssignmentStatus.ACTIVE, overdue=view == "overdue"
        )
    if view == "completed-reviews":
        return _assignment_filter(user_id, is_qc_member, AssignmentStatus.APPROVED)
    if view == "returned-by-me":
        return _assignment_filter(user_id, is_qc_member, AssignmentStatus.RETURNED)
    raise ValueError("Unknown report view.")


def _visibility_filter(profile: User, user_id: UUID, is_qc_member: bool) -> ColumnElement[bool]:
    if "report.view_all" in profile.permissions:
        return true()
    creator_access = (
        or_(
            DatabaseReport.owner_user_id == user_id,
            DatabaseReport.created_by_user_id == user_id,
        )
        if is_report_creator(profile)
        else false()
    )
    runtime_assignment = or_(
        *(
            _assignment_filter(user_id, is_qc_member, status)
            for status in (
                AssignmentStatus.ACTIVE,
                AssignmentStatus.APPROVED,
                AssignmentStatus.RETURNED,
            )
        )
    )
    from app.database.models.report import ReportWorkflowAssignment

    permission_approver = (
        select(ReportWorkflowAssignment.id)
        .where(
            ReportWorkflowAssignment.report_id == DatabaseReport.id,
            ReportWorkflowAssignment.user_id == user_id,
            ReportWorkflowAssignment.role_key_snapshot.in_(
                ("director", "director_level", "partner")
            ),
        )
        .exists()
    )
    return or_(creator_access, runtime_assignment, permission_approver)


async def _pending_approval_count(session: object, user_id: UUID, is_qc_member: bool) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    result = await session.scalar(
        select(func.count(func.distinct(DatabaseReport.id)))
        .select_from(DatabaseReport)
        .join(
            ReportRevision,
            and_(
                ReportRevision.report_id == DatabaseReport.id,
                ReportRevision.revision_number == DatabaseReport.current_revision_number,
            ),
        )
        .join(
            ApprovalCycle,
            and_(
                ApprovalCycle.report_revision_id == ReportRevision.id,
                ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ),
        )
        .join(
            ReportApprovalStage,
            and_(
                ReportApprovalStage.approval_cycle_id == ApprovalCycle.id,
                ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
            ),
        )
        .join(
            ReportStageAssignment,
            and_(
                ReportStageAssignment.report_approval_stage_id == ReportApprovalStage.id,
                or_(
                    ReportStageAssignment.user_id == user_id,
                    and_(
                        true() if is_qc_member else false(),
                        ReportStageAssignment.role_key_snapshot == "qc",
                    ),
                ),
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
            ),
        )
        .where(DatabaseReport.status == ReportStatus.IN_REVIEW)
    )
    return int(result or 0)


async def _overdue_approval_count(
    session: object,
    user_id: UUID,
    is_qc_member: bool,
    current_time: datetime,
) -> int:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    result = await session.scalar(
        select(func.count(func.distinct(DatabaseReport.id)))
        .select_from(DatabaseReport)
        .join(
            ReportRevision,
            and_(
                ReportRevision.report_id == DatabaseReport.id,
                ReportRevision.revision_number == DatabaseReport.current_revision_number,
            ),
        )
        .join(
            ApprovalCycle,
            and_(
                ApprovalCycle.report_revision_id == ReportRevision.id,
                ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ),
        )
        .join(
            ReportApprovalStage,
            and_(
                ReportApprovalStage.approval_cycle_id == ApprovalCycle.id,
                ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
                ReportApprovalStage.due_at.is_not(None),
                ReportApprovalStage.due_at < current_time,
            ),
        )
        .join(
            ReportStageAssignment,
            and_(
                ReportStageAssignment.report_approval_stage_id == ReportApprovalStage.id,
                or_(
                    ReportStageAssignment.user_id == user_id,
                    and_(
                        true() if is_qc_member else false(),
                        ReportStageAssignment.role_key_snapshot == "qc",
                    ),
                ),
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
            ),
        )
    )
    return int(result or 0)


def _metrics(
    profile: User,
    status_counts: dict[ReportStatus, int],
    owner_counts: dict[ReportStatus, int],
    pending_count: int,
    overdue_count: int,
    approved_month_count: int,
    review_activity: tuple[int, int],
    generated_at: datetime,
) -> tuple[DashboardMetric, ...]:
    draft_count = status_counts.get(ReportStatus.DRAFT, 0)
    returned_count = status_counts.get(ReportStatus.RETURNED, 0)
    review_count = status_counts.get(ReportStatus.IN_REVIEW, 0)
    owner_drafts = owner_counts.get(ReportStatus.DRAFT, 0)
    owner_returned = owner_counts.get(ReportStatus.RETURNED, 0)
    owner_review = owner_counts.get(ReportStatus.IN_REVIEW, 0)
    active_count = draft_count + returned_count + review_count
    if is_report_creator(profile) and is_report_reviewer(profile):
        return (
            DashboardMetric(
                "My Active Reports",
                owner_drafts + owner_returned + owner_review,
                f"{owner_drafts} draft · {owner_returned} returned · {owner_review} awaiting",
                "description",
                "blue",
            ),
            DashboardMetric(
                "My Drafts",
                owner_drafts,
                "Reports still being prepared",
                "edit_note",
                "amber",
            ),
            DashboardMetric(
                "Pending My Approval",
                pending_count,
                f"{overdue_count} overdue" if overdue_count else "No overdue approvals",
                "approval",
                "amber",
            ),
            DashboardMetric(
                "Returned for Changes",
                owner_returned,
                "Reports requiring your response",
                "assignment_return",
                "teal",
            ),
            DashboardMetric(
                "Awaiting Approval",
                owner_review,
                "Reports currently with selected reviewers",
                "hourglass_top",
                "green",
            ),
        )
    if is_report_reviewer(profile):
        completed_reviews, returned_reviews = review_activity
        return (
            DashboardMetric(
                "Pending My Approval",
                pending_count,
                f"{overdue_count} overdue" if overdue_count else "No overdue approvals",
                "approval",
                "amber",
            ),
            DashboardMetric(
                "Overdue Approvals",
                overdue_count,
                "Assigned active stages past their due date",
                "schedule",
                "amber",
            ),
            DashboardMetric(
                "Completed Reviews",
                completed_reviews,
                "Reports approved at your assigned stage",
                "task_alt",
                "green",
            ),
            DashboardMetric(
                "Returned by Me",
                returned_reviews,
                "Reports returned with a required reason",
                "assignment_return",
                "teal",
            ),
        )
    if is_report_creator(profile):
        return (
            DashboardMetric(
                "My Active Reports",
                owner_drafts + owner_returned + owner_review,
                f"{owner_drafts} draft · {owner_returned} returned · {owner_review} awaiting",
                "description",
                "blue",
            ),
            DashboardMetric(
                "My Drafts",
                owner_drafts,
                "Reports still being prepared",
                "edit_note",
                "amber",
            ),
            DashboardMetric(
                "Awaiting Approval",
                owner_review,
                "Submitted to the selected approval path",
                "hourglass_top",
                "teal",
            ),
            DashboardMetric(
                "Returned for Changes",
                owner_returned,
                "Reports requiring your response",
                "assignment_return",
                "amber",
            ),
            DashboardMetric(
                "Approved This Month",
                approved_month_count,
                f"Since {_month_start_local(generated_at):%d %b %Y}",
                "task_alt",
                "green",
            ),
        )
    return (
        DashboardMetric(
            "Active Reports",
            active_count,
            f"{draft_count} draft · {returned_count} returned · {review_count} in review",
            "description",
            "blue",
        ),
        DashboardMetric(
            "Drafts to Complete",
            owner_drafts + owner_returned,
            f"{owner_drafts} draft · {owner_returned} returned",
            "edit_note",
            "amber",
        ),
        DashboardMetric(
            "Pending My Approval",
            pending_count,
            f"{overdue_count} overdue" if overdue_count else "No overdue approvals",
            "approval",
            "amber",
        ),
        DashboardMetric(
            "Reports in Review",
            review_count,
            "Across active approval stages",
            "rate_review",
            "teal",
        ),
        DashboardMetric(
            "Approved This Month",
            approved_month_count,
            f"Since {_month_start_local(generated_at):%d %b %Y}",
            "task_alt",
            "green",
        ),
    )


async def _attention_items(
    session: object,
    user_id: UUID,
    profile: User,
    is_qc_member: bool,
    current_time: datetime,
    limit: int,
) -> tuple[AttentionItem, ...]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    active_rows = await session.execute(
        select(
            DatabaseReport.id,
            DatabaseReport.public_id,
            DatabaseReport.title,
            DatabaseReport.entity_name,
            ReportApprovalStage.display_name_snapshot,
            ReportApprovalStage.due_at,
            ReportApprovalStage.required_permission_snapshot,
            _received_from_expression(),
        )
        .select_from(DatabaseReport)
        .join(
            ReportRevision,
            and_(
                ReportRevision.report_id == DatabaseReport.id,
                ReportRevision.revision_number == DatabaseReport.current_revision_number,
            ),
        )
        .join(
            ApprovalCycle,
            and_(
                ApprovalCycle.report_revision_id == ReportRevision.id,
                ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ),
        )
        .join(
            ReportApprovalStage,
            and_(
                ReportApprovalStage.approval_cycle_id == ApprovalCycle.id,
                ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
            ),
        )
        .join(
            ReportStageAssignment,
            and_(
                ReportStageAssignment.report_approval_stage_id == ReportApprovalStage.id,
                or_(
                    ReportStageAssignment.user_id == user_id,
                    and_(
                        true() if is_qc_member else false(),
                        ReportStageAssignment.role_key_snapshot == "qc",
                    ),
                ),
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
            ),
        )
        .where(DatabaseReport.status == ReportStatus.IN_REVIEW)
        .order_by(
            ReportApprovalStage.due_at.is_(None),
            ReportApprovalStage.due_at,
            DatabaseReport.last_activity_at.desc(),
        )
        .limit(limit)
    )
    items: list[AttentionItem] = []
    for (
        _report_id,
        public_id,
        title,
        entity_name,
        stage_name,
        due_at,
        permission,
        received_from,
    ) in active_rows:
        can_approve = permission == "report.approve" and "report.approve" in profile.permissions
        actions = ("Open", "Review", "Approve", "Return") if can_approve else ("Open", "Review")
        due_date = cast(datetime | None, due_at)
        items.append(
            AttentionItem(
                report_id=public_id,
                report=title,
                client=entity_name or "Entity name not entered",
                received_from=received_from,
                required_action=f"Complete {stage_name}",
                due_date=due_date.date() if due_date else None,
                current_stage=stage_name,
                priority=_priority(due_date, current_time),
                permitted_actions=actions,
            )
        )

    remaining = max(0, limit - len(items))
    if remaining and (is_report_creator(profile) or "report.view_all" in profile.permissions):
        returned_rows = await session.execute(
            select(
                DatabaseReport.id,
                DatabaseReport.public_id,
                DatabaseReport.title,
                DatabaseReport.entity_name,
                _received_from_expression(),
            )
            .where(
                DatabaseReport.owner_user_id == user_id,
                DatabaseReport.status == ReportStatus.RETURNED,
            )
            .order_by(DatabaseReport.last_activity_at.desc())
            .limit(remaining)
        )
        for _report_id, public_id, title, entity_name, received_from in returned_rows:
            items.append(
                AttentionItem(
                    report_id=public_id,
                    report=title,
                    client=entity_name or "Entity name not entered",
                    received_from=received_from,
                    required_action="Respond to review changes",
                    due_date=None,
                    current_stage="Returned for Changes",
                    priority="High",
                    permitted_actions=("Open",),
                )
            )
    return tuple(items)


async def _review_activity_counts(
    session: object, user_id: UUID, is_qc_member: bool
) -> tuple[int, int]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    rows = await session.execute(
        select(ReportStageAssignment.status, func.count(func.distinct(ReportRevision.report_id)))
        .join(
            ReportApprovalStage,
            ReportApprovalStage.id == ReportStageAssignment.report_approval_stage_id,
        )
        .join(ApprovalCycle, ApprovalCycle.id == ReportApprovalStage.approval_cycle_id)
        .join(ReportRevision, ReportRevision.id == ApprovalCycle.report_revision_id)
        .where(
            or_(
                ReportStageAssignment.user_id == user_id,
                and_(
                    true() if is_qc_member else false(),
                    ReportStageAssignment.role_key_snapshot == "qc",
                ),
            ),
            ReportStageAssignment.status.in_(
                (AssignmentStatus.APPROVED, AssignmentStatus.RETURNED)
            ),
        )
        .group_by(ReportStageAssignment.status)
    )
    counts = {status: int(count) for status, count in rows}
    return (
        counts.get(AssignmentStatus.APPROVED, 0),
        counts.get(AssignmentStatus.RETURNED, 0),
    )


def _received_from_expression() -> ColumnElement[str]:
    # Keep the existing newest-approval ordering and creator fallback, but include
    # attribution in the bounded attention query instead of making per-row round trips.
    previous_actor = (
        select(ApplicationUser.display_name)
        .join(ApprovalAction, ApprovalAction.actor_user_id == ApplicationUser.id)
        .where(
            ApprovalAction.report_id == DatabaseReport.id,
            ApprovalAction.action == ApprovalActionType.APPROVED,
        )
        .order_by(ApprovalAction.action_at.desc())
        .limit(1)
        .correlate(DatabaseReport)
        .scalar_subquery()
    )
    creator = (
        select(ApplicationUser.display_name)
        .where(ApplicationUser.id == DatabaseReport.created_by_user_id)
        .correlate(DatabaseReport)
        .scalar_subquery()
    )
    return func.coalesce(func.nullif(previous_actor, ""), func.nullif(creator, ""), "Unknown user")


async def _recent_reports(
    session: object,
    visible: ColumnElement[bool],
    limit: int,
    *,
    offset: int = 0,
) -> tuple[Report, ...]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    owner = aliased(ApplicationUser)
    active_stage = (
        select(ReportApprovalStage.display_name_snapshot)
        .select_from(ReportRevision)
        .join(
            ApprovalCycle,
            and_(
                ApprovalCycle.report_revision_id == ReportRevision.id,
                ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
            ),
        )
        .join(
            ReportApprovalStage,
            and_(
                ReportApprovalStage.approval_cycle_id == ApprovalCycle.id,
                ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
            ),
        )
        .where(
            ReportRevision.report_id == DatabaseReport.id,
            ReportRevision.revision_number == DatabaseReport.current_revision_number,
        )
        .limit(1)
        .correlate(DatabaseReport)
        .scalar_subquery()
    )
    rows = await session.execute(
        select(
            DatabaseReport,
            ReportTemplate.name,
            owner.display_name,
            active_stage.label("active_stage"),
        )
        .join(
            ReportTemplateVersion,
            ReportTemplateVersion.id == DatabaseReport.template_version_id,
        )
        .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
        .join(owner, owner.id == DatabaseReport.owner_user_id)
        .where(visible)
        .order_by(DatabaseReport.last_activity_at.desc(), DatabaseReport.public_id.desc())
        .limit(limit)
        .offset(offset)
    )
    return tuple(
        Report(
            report_id=report.public_id,
            name=report.title,
            client=report.entity_name or "Entity name not entered",
            report_type=template_name,
            status=(
                active_stage_name
                if report.status is ReportStatus.IN_REVIEW and active_stage_name
                else _status_label(report.status)
            ),
            last_updated=report.last_activity_at,
            owner=owner_name,
        )
        for report, template_name, owner_name, active_stage_name in rows
    )


async def _workflow_stages(
    session: object,
    visible: ColumnElement[bool],
    *,
    status_counts: Mapping[ReportStatus, int] | None = None,
) -> tuple[WorkflowStage, ...]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    if status_counts is None:
        status_counts = {
            status: int(count)
            for status, count in await session.execute(
                select(DatabaseReport.status, func.count(DatabaseReport.id))
                .where(
                    visible,
                    DatabaseReport.status.in_(
                        (ReportStatus.DRAFT, ReportStatus.RETURNED, *COMPLETED_STATUSES)
                    ),
                )
                .group_by(DatabaseReport.status)
            )
        }
    configured_stages = tuple(
        await session.execute(
            select(
                ApprovalStepDefinition.display_name,
                func.min(ApprovalStepDefinition.sequence),
            )
            .join(
                ApprovalWorkflowVersion,
                ApprovalWorkflowVersion.id == ApprovalStepDefinition.workflow_version_id,
            )
            .where(ApprovalWorkflowVersion.status == PublicationStatus.ACTIVE)
            .group_by(ApprovalStepDefinition.display_name)
            .order_by(func.min(ApprovalStepDefinition.sequence))
        )
    )
    stage_counts = {
        name: int(count)
        for name, count in await session.execute(
            select(
                ReportApprovalStage.display_name_snapshot,
                func.count(func.distinct(DatabaseReport.id)),
            )
            .select_from(DatabaseReport)
            .join(
                ReportRevision,
                and_(
                    ReportRevision.report_id == DatabaseReport.id,
                    ReportRevision.revision_number == DatabaseReport.current_revision_number,
                ),
            )
            .join(
                ApprovalCycle,
                and_(
                    ApprovalCycle.report_revision_id == ReportRevision.id,
                    ApprovalCycle.status == ApprovalCycleStatus.ACTIVE,
                ),
            )
            .join(
                ReportApprovalStage,
                and_(
                    ReportApprovalStage.approval_cycle_id == ApprovalCycle.id,
                    ReportApprovalStage.status == ApprovalStageStatus.ACTIVE,
                ),
            )
            .where(visible, DatabaseReport.status == ReportStatus.IN_REVIEW)
            .group_by(ReportApprovalStage.display_name_snapshot)
        )
    }
    stages = [
        WorkflowStage("Draft", status_counts.get(ReportStatus.DRAFT, 0), "edit_note"),
        WorkflowStage("Returned", status_counts.get(ReportStatus.RETURNED, 0), "assignment_return"),
    ]
    names = tuple(
        dict.fromkeys([name for name, _sequence in configured_stages] + list(stage_counts))
    )
    stages.extend(
        WorkflowStage(name, stage_counts.get(name, 0), _stage_icon(name)) for name in names
    )
    stages.append(
        WorkflowStage(
            "Approved",
            sum(status_counts.get(status, 0) for status in COMPLETED_STATUSES),
            "task_alt",
        )
    )
    return tuple(stages)


def _priority(due_at: datetime | None, current_time: datetime) -> str:
    if due_at is None:
        return "Normal"
    if due_at <= current_time + timedelta(days=1):
        return "High"
    if due_at <= current_time + timedelta(days=3):
        return "Medium"
    return "Normal"


def _status_label(status: ReportStatus) -> str:
    return {
        ReportStatus.DRAFT: "Draft",
        ReportStatus.IN_REVIEW: "In Review",
        ReportStatus.RETURNED: "Returned for Changes",
        ReportStatus.REJECTED: "Rejected",
        ReportStatus.APPROVED: "Approved",
        ReportStatus.FINALIZED: "Finalized",
        ReportStatus.ARCHIVED: "Archived",
        ReportStatus.CANCELLED: "Cancelled",
    }[status]


def _stage_icon(name: str) -> str:
    normalized = name.casefold()
    if "manager" in normalized or "director" in normalized:
        return "manage_accounts"
    if "partner" in normalized:
        return "approval"
    if "eqcr" in normalized:
        return "verified"
    if "qc" in normalized:
        return "fact_check"
    return "rate_review"


def _month_start_local(current_time: datetime) -> datetime:
    localized = current_time.astimezone(INDIA_TIMEZONE)
    return localized.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _month_start_utc(current_time: datetime) -> datetime:
    return _month_start_local(current_time).astimezone(UTC)


def _next_month_start_utc(current_time: datetime) -> datetime:
    month_start = _month_start_local(current_time)
    return (month_start + timedelta(days=32)).replace(day=1).astimezone(UTC)
