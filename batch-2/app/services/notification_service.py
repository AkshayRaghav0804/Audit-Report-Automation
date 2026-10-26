"""Persistent workflow notifications and durable Microsoft Graph email requests."""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.models.approval import ApprovalAction, ReportApprovalStage, ReportStageAssignment
from app.database.models.enums import ApprovalActionType, AssignmentStatus, ReportStatus
from app.database.models.mixins import utc_now
from app.database.models.notification import EmailOutbox, UserNotification
from app.database.models.report import Report, ReportRevision
from app.database.session import DatabaseManager, database
from app.models.notification import Notification


@dataclass(frozen=True, slots=True)
class NotificationInbox:
    notifications: tuple[Notification, ...]
    unread_count: int


class NotificationService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def list_for_user(self, email: str, *, limit: int = 100) -> tuple[Notification, ...]:
        return (await self.inbox_for_user(email, limit=limit)).notifications

    async def inbox_for_user(self, email: str, *, limit: int = 5) -> NotificationInbox:
        """Load the visible menu rows and total unread count in one database round trip."""

        async with self.manager.session() as session:
            unread_count = (
                func.count(UserNotification.id).filter(UserNotification.is_read.is_(False)).over()
            )
            rows = tuple(
                await session.execute(
                    select(UserNotification, Report.public_id, unread_count)
                    .join(
                        ApplicationUser,
                        ApplicationUser.id == UserNotification.recipient_user_id,
                    )
                    .outerjoin(Report, Report.id == UserNotification.report_id)
                    .where(ApplicationUser.email == email.casefold())
                    .order_by(UserNotification.created_at.desc())
                    .limit(limit)
                )
            )
            notifications = tuple(
                Notification(
                    notification_id=str(record.id),
                    notification_type=record.notification_type,
                    title=record.title,
                    message=record.message,
                    created_at=record.created_at.astimezone().strftime("%d %b %Y, %I:%M %p"),
                    is_read=record.is_read,
                    report_id=public_id,
                    target_path=record.target_path,
                )
                for record, public_id, _unread_count in rows
            )
            return NotificationInbox(
                notifications=notifications,
                unread_count=int(rows[0][2]) if rows else 0,
            )

    async def mark_read(self, email: str, notification_id: str) -> None:
        try:
            identifier = UUID(notification_id)
        except ValueError:
            return
        async with self.manager.session() as session:
            async with session.begin():
                actor_id = await session.scalar(
                    select(ApplicationUser.id).where(ApplicationUser.email == email.casefold())
                )
                if actor_id is None:
                    return
                await session.execute(
                    update(UserNotification)
                    .where(
                        UserNotification.id == identifier,
                        UserNotification.recipient_user_id == actor_id,
                    )
                    .values(is_read=True, read_at=utc_now())
                )

    async def mark_all_read(self, email: str) -> None:
        async with self.manager.session() as session:
            async with session.begin():
                actor_id = await session.scalar(
                    select(ApplicationUser.id).where(ApplicationUser.email == email.casefold())
                )
                if actor_id is None:
                    return
                await session.execute(
                    update(UserNotification)
                    .where(
                        UserNotification.recipient_user_id == actor_id,
                        UserNotification.is_read.is_(False),
                    )
                    .values(is_read=True, read_at=utc_now())
                )


async def notify_stage_activated(
    session: AsyncSession,
    report: Report,
    stage: ReportApprovalStage,
    *,
    received_from: str,
) -> None:
    # Sessions deliberately disable autoflush. Include assignments activated in
    # this transaction before reading the next reviewers.
    await session.flush()
    recipients, delivery_emails = await _stage_recipients(session, stage)
    stage_label = (
        "QC Team Review"
        if stage.required_role_key_snapshot == "qc"
        else stage.display_name_snapshot
    )
    message = f"{report.public_id} is waiting for {stage_label}. Received from {received_from}."
    for recipient in recipients:
        _add_notification(
            session,
            recipient.id,
            report,
            "reviewer_assigned",
            "Report assigned for approval",
            message,
        )
    for delivery_email in delivery_emails:
        _queue_email(
            session,
            report,
            delivery_email,
            f"Approval required: {report.public_id}",
            message,
        )


async def notify_stage_approved(
    session: AsyncSession,
    report: Report,
    stage: ReportApprovalStage,
    *,
    actor_label: str,
) -> None:
    maker = await session.get(ApplicationUser, report.created_by_user_id)
    if maker is None:
        return
    _add_notification(
        session,
        maker.id,
        report,
        "report_approved",
        f"{stage.display_name_snapshot} approved",
        f"{actor_label} approved {report.public_id} at {stage.display_name_snapshot}.",
    )


async def notify_report_returned(
    session: AsyncSession,
    report: Report,
    stage: ReportApprovalStage,
    *,
    actor_label: str,
    reason: str,
) -> None:
    maker = await session.get(ApplicationUser, report.created_by_user_id)
    if maker is None:
        return
    message = (
        f"{actor_label} returned {report.public_id} from {stage.display_name_snapshot}. "
        f"Reason: {reason}"
    )
    _add_notification(
        session,
        maker.id,
        report,
        "report_returned",
        "Report returned for changes",
        message,
    )
    _queue_email(
        session,
        report,
        maker.email,
        f"Report returned: {report.public_id}",
        message,
    )


async def notify_report_completed(session: AsyncSession, report: Report) -> None:
    maker = await session.get(ApplicationUser, report.created_by_user_id)
    if maker is None:
        return
    message = f"All required approval stages are complete for {report.public_id}."
    _add_notification(
        session,
        maker.id,
        report,
        "report_approved",
        "Report fully approved",
        message,
    )
    _queue_email(
        session,
        report,
        maker.email,
        f"Report approved: {report.public_id}",
        message,
    )


async def notify_comment_added(
    session: AsyncSession,
    report: Report,
    *,
    section_key: str,
    actor: ApplicationUser,
    actor_label: str,
    stage: ReportApprovalStage | None,
) -> None:
    recipients = await _comment_recipients(session, report, actor=actor, stage=stage)
    message = (
        f"{actor_label} added a comment to {report.public_id} "
        f"in {section_key.replace('_', ' ')}. Open the report to read the comment."
    )
    delivery_emails: set[str] = set()
    for recipient in recipients:
        if not recipient.active or recipient.id == actor.id:
            continue
        email = recipient.email.casefold()
        if email == actor.email.casefold():
            continue
        _add_notification(
            session,
            recipient.id,
            report,
            "comment_added",
            "Report comment added",
            message,
            target_path=f"/reports/{report.public_id}?{urlencode({'section': section_key})}",
        )
        if email not in delivery_emails:
            _queue_email(session, report, email, f"Comment added: {report.public_id}", message)
            delivery_emails.add(email)


async def _comment_recipients(
    session: AsyncSession,
    report: Report,
    *,
    actor: ApplicationUser,
    stage: ReportApprovalStage | None,
) -> tuple[ApplicationUser, ...]:
    maker = await session.get(ApplicationUser, report.created_by_user_id)
    recipients = {maker.id: maker} if maker is not None else {}
    if actor.id in {report.created_by_user_id, report.owner_user_id}:
        if stage is not None:
            # Reply to the selected active reviewers, including the selected QC
            # mailbox, without broadcasting email to the entire QC directory.
            reviewers = await session.scalars(
                select(ApplicationUser)
                .join(ReportStageAssignment, ReportStageAssignment.user_id == ApplicationUser.id)
                .where(
                    ReportStageAssignment.report_approval_stage_id == stage.id,
                    ReportStageAssignment.status == AssignmentStatus.ACTIVE,
                )
            )
            recipients.update((reviewer.id, reviewer) for reviewer in reviewers)
        elif report.status in {ReportStatus.RETURNED, ReportStatus.APPROVED}:
            # There is no active assignment after a return or final approval.
            # Send the creator's follow-up to the person who last took that action.
            action_type = (
                ApprovalActionType.RETURNED
                if report.status == ReportStatus.RETURNED
                else ApprovalActionType.APPROVED
            )
            reviewer = await session.scalar(
                select(ApplicationUser)
                .join(ApprovalAction, ApprovalAction.actor_user_id == ApplicationUser.id)
                .join(ReportRevision, ReportRevision.id == ApprovalAction.report_revision_id)
                .where(
                    ApprovalAction.report_id == report.id,
                    ReportRevision.revision_number == report.current_revision_number,
                    ApprovalAction.action == action_type,
                )
                .order_by(ApprovalAction.action_at.desc())
                .limit(1)
            )
            if reviewer is not None:
                recipients[reviewer.id] = reviewer
    return tuple(recipients.values())


def _add_notification(
    session: AsyncSession,
    recipient_user_id: UUID,
    report: Report,
    notification_type: str,
    title: str,
    message: str,
    *,
    target_path: str | None = None,
) -> None:
    session.add(
        UserNotification(
            recipient_user_id=recipient_user_id,
            report_id=report.id,
            notification_type=notification_type,
            title=title,
            message=message,
            target_path=target_path or f"/reports/{report.public_id}",
        )
    )


def _queue_email(
    session: AsyncSession,
    report: Report,
    to_address: str,
    subject: str,
    message: str,
) -> None:
    settings = Settings.from_environment()
    text_body, html_body = email_bodies(message, report.public_id, settings.application_base_url)
    session.add(
        EmailOutbox(
            report_id=report.id,
            to_address=to_address.casefold(),
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            status="pending",
            attempt_count=0,
        )
    )


def email_bodies(message: str, public_id: str, application_base_url: str) -> tuple[str, str]:
    report_url = f"{application_base_url.rstrip('/')}/reports/{public_id}"
    return (
        f"{message}\n\nOpen the secure report: {report_url}",
        f"<p>{escape(message)}</p>"
        f'<p><a href="{escape(report_url)}">Open the secure report</a></p>'
        "<p>Microsoft sign-in and report access are required.</p>",
    )


async def _stage_recipients(
    session: AsyncSession, stage: ReportApprovalStage
) -> tuple[tuple[ApplicationUser, ...], tuple[str, ...]]:
    users = tuple(
        await session.scalars(
            select(ApplicationUser)
            .join(ReportStageAssignment, ReportStageAssignment.user_id == ApplicationUser.id)
            .where(
                ReportStageAssignment.report_approval_stage_id == stage.id,
                ReportStageAssignment.status == AssignmentStatus.ACTIVE,
                ApplicationUser.active.is_(True),
            )
            .order_by(ReportStageAssignment.assignment_order)
        )
    )
    # The addresses selected for this report determine email recipients at every
    # stage, including QC. Global mailbox settings must not redirect approval mail.
    delivery_emails = tuple(dict.fromkeys(user.email.casefold() for user in users))
    if stage.required_role_key_snapshot == "qc":
        members = tuple(
            await session.scalars(
                select(ApplicationUser)
                .join(UserRole, UserRole.user_id == ApplicationUser.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(
                    Role.key == "qc",
                    Role.active.is_(True),
                    ApplicationUser.active.is_(True),
                )
                .order_by(ApplicationUser.display_name)
            )
        )
        return members, delivery_emails
    return tuple(users), delivery_emails
