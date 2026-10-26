"""Section permissions scoped to one report, section and requesting person."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import ApplicationUser
from app.database.models.enums import ReportStatus
from app.database.models.mixins import utc_now
from app.database.models.notification import UserNotification
from app.database.models.report import Report, ReportSectionChange, ReportWorkflowAssignment
from app.database.models.section_access import ReportSectionLock, SectionEditRequest
from app.database.models.template import TemplateSection
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.report_access_service import ReportAccessService, _actor_has_role
from app.services.report_collaboration_service import _actor, _report_revision_section


@dataclass(frozen=True)
class SectionAccess:
    locked: bool = True
    can_edit: bool = False
    can_manage: bool = False
    can_decide: bool = False
    request_status: str | None = None
    pending_requests: int = 0


async def lock_report(session: AsyncSession, public_id: str) -> Report:
    report = await session.scalar(
        select(Report).where(Report.public_id == public_id).with_for_update()
    )
    if report is None:
        raise ValueError("Report not found.")
    if report.status not in {ReportStatus.DRAFT, ReportStatus.RETURNED, ReportStatus.IN_REVIEW}:
        raise PermissionError("This report is no longer editable.")
    return report


async def section_is_editable(
    session: AsyncSession, report_id: UUID, section_id: UUID, actor_id: UUID
) -> bool:
    lock = await session.scalar(
        select(ReportSectionLock).where(
            ReportSectionLock.report_id == report_id,
            ReportSectionLock.template_section_id == section_id,
        )
    )
    if lock is not None and not lock.locked:
        return True
    return bool(
        await session.scalar(
            select(SectionEditRequest.id).where(
                SectionEditRequest.report_id == report_id,
                SectionEditRequest.template_section_id == section_id,
                SectionEditRequest.requester_user_id == actor_id,
                SectionEditRequest.status == "approved",
                SectionEditRequest.expired_at.is_(None),
            )
        )
    )


async def expire_requests(session: AsyncSession, report_id: UUID) -> None:
    await session.execute(
        update(SectionEditRequest)
        .where(
            SectionEditRequest.report_id == report_id,
            SectionEditRequest.status.in_(("pending", "approved")),
            SectionEditRequest.expired_at.is_(None),
        )
        .values(expired_at=utc_now())
    )


async def _approvers(session: AsyncSession, report_id: UUID) -> set[UUID]:
    return set(
        await session.scalars(
            select(ReportWorkflowAssignment.user_id)
            .join(ApplicationUser, ApplicationUser.id == ReportWorkflowAssignment.user_id)
            .where(
                ReportWorkflowAssignment.report_id == report_id,
                ReportWorkflowAssignment.role_key_snapshot.in_(
                    ("director", "director_level", "partner")
                ),
                ApplicationUser.active.is_(True),
            )
        )
    )


def _notify(session: AsyncSession, report: Report, recipient: UUID, message: str) -> None:
    session.add(
        UserNotification(
            recipient_user_id=recipient,
            report_id=report.id,
            notification_type="section_edit_permission",
            title="Section editing permission",
            message=message,
            target_path=f"/reports/{report.public_id}",
        )
    )


class SectionAccessService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def states(self, public_id: str, profile: User) -> dict[str, SectionAccess]:
        access = await ReportAccessService(self.manager).access_for(public_id, profile)
        if not access.can_view:
            raise PermissionError("You do not have access to this report.")
        async with self.manager.session() as session:
            actor = await _actor(session, profile.email)
            report = await session.scalar(select(Report).where(Report.public_id == public_id))
            assert report is not None
            manage = await _actor_has_role(session, actor.id, "administrator")
            decide = actor.id in await _approvers(session, report.id)
            sections = await session.scalars(
                select(TemplateSection).where(
                    TemplateSection.template_version_id == report.template_version_id
                )
            )
            locks = {
                item.template_section_id: item.locked
                for item in await session.scalars(
                    select(ReportSectionLock).where(ReportSectionLock.report_id == report.id)
                )
            }
            requests = list(
                await session.scalars(
                    select(SectionEditRequest)
                    .where(SectionEditRequest.report_id == report.id)
                    .order_by(SectionEditRequest.created_at.desc(), SectionEditRequest.id)
                )
            )
            result = {}
            for section in sections:
                own_requests = [
                    item
                    for item in requests
                    if item.template_section_id == section.id and item.requester_user_id == actor.id
                ]
                request = own_requests[0] if own_requests else None
                locked = locks.get(section.id, True)
                approved = any(
                    item.status == "approved" and item.expired_at is None for item in own_requests
                )
                result[section.section_key] = SectionAccess(
                    locked=locked,
                    can_edit=access.can_edit_fixed_sections and (not locked or approved),
                    can_manage=manage and access.can_edit_fixed_sections,
                    can_decide=decide
                    and report.status
                    in {ReportStatus.DRAFT, ReportStatus.RETURNED, ReportStatus.IN_REVIEW},
                    request_status=(
                        "expired"
                        if request and request.expired_at
                        else request.status
                        if request
                        else None
                    ),
                    pending_requests=sum(
                        item.template_section_id == section.id
                        and item.requester_user_id != actor.id
                        and item.status == "pending"
                        and item.expired_at is None
                        for item in requests
                    )
                    if decide
                    else 0,
                )
            return result

    async def set_lock(
        self, public_id: str, template_key: str, section_key: str, profile: User, locked: bool
    ) -> None:
        async with self.manager.session() as session, session.begin():
            actor = await _actor(session, profile.email)
            if not await _actor_has_role(session, actor.id, "administrator"):
                raise PermissionError("Only administrators can lock or unlock sections.")
            report = await lock_report(session, public_id)
            _, revision, section = await _report_revision_section(
                session, public_id, template_key, section_key
            )
            lock = await session.scalar(
                select(ReportSectionLock).where(
                    ReportSectionLock.report_id == report.id,
                    ReportSectionLock.template_section_id == section.id,
                )
            )
            previous = lock is None or lock.locked
            if previous == locked:
                return
            if lock is None:
                session.add(
                    ReportSectionLock(
                        report_id=report.id,
                        template_section_id=section.id,
                        locked=locked,
                        changed_by_user_id=actor.id,
                    )
                )
            else:
                lock.locked = locked
                lock.changed_by_user_id = actor.id
            await session.execute(
                update(SectionEditRequest)
                .where(
                    SectionEditRequest.report_id == report.id,
                    SectionEditRequest.template_section_id == section.id,
                    SectionEditRequest.expired_at.is_(None),
                    SectionEditRequest.status.in_(("pending", "approved")),
                )
                .values(expired_at=utc_now())
            )
            session.add(
                ReportSectionChange(
                    report_id=report.id,
                    report_revision_id=revision.id,
                    template_section_id=section.id,
                    previous_content="Locked" if previous else "Unlocked",
                    new_content="Locked" if locked else "Unlocked",
                    change_target="lock",
                    reason="Administrator changed this report section's lock.",
                    changed_by_user_id=actor.id,
                    actor_label_snapshot=actor.display_name,
                    actor_role_snapshot="Administrator",
                    stage_label_snapshot="Section permissions",
                )
            )
            report.last_activity_at = utc_now()

    async def request(
        self, public_id: str, template_key: str, section_key: str, profile: User, reason: str
    ) -> None:
        access = await ReportAccessService(self.manager).access_for(public_id, profile)
        if not access.can_edit_fixed_sections:
            raise PermissionError("You cannot request editing access to this report.")
        if not reason.strip():
            raise ValueError("Explain why fixed wording needs to change.")
        async with self.manager.session() as session, session.begin():
            report = await lock_report(session, public_id)
            actor = await _actor(session, profile.email)
            _, _, section = await _report_revision_section(
                session, public_id, template_key, section_key
            )
            if await section_is_editable(session, report.id, section.id, actor.id):
                raise ValueError("You already have editing access to this section.")
            existing = await session.scalar(
                select(SectionEditRequest.id).where(
                    SectionEditRequest.report_id == report.id,
                    SectionEditRequest.template_section_id == section.id,
                    SectionEditRequest.requester_user_id == actor.id,
                    SectionEditRequest.status == "pending",
                    SectionEditRequest.expired_at.is_(None),
                )
            )
            if existing:
                raise ValueError("An editing request is already pending for this section.")
            approvers = await _approvers(session, report.id) - {actor.id}
            if not approvers:
                raise ValueError("Another assigned Director or Partner is required to approve.")
            session.add(
                SectionEditRequest(
                    report_id=report.id,
                    template_section_id=section.id,
                    requester_user_id=actor.id,
                    reason=reason.strip(),
                )
            )
            for recipient in approvers:
                _notify(
                    session,
                    report,
                    recipient,
                    f"{actor.display_name} requests fixed-wording access to {section.title}.",
                )
            report.last_activity_at = utc_now()

    async def pending(
        self, public_id: str, section_key: str, profile: User
    ) -> list[tuple[UUID, str, str]]:
        async with self.manager.session() as session:
            actor = await _actor(session, profile.email)
            report = await session.scalar(select(Report).where(Report.public_id == public_id))
            if report is None or actor.id not in await _approvers(session, report.id):
                raise PermissionError("Only this report's Director or Partner can decide requests.")
            rows = await session.execute(
                select(
                    SectionEditRequest.id, ApplicationUser.display_name, SectionEditRequest.reason
                )
                .join(ApplicationUser, ApplicationUser.id == SectionEditRequest.requester_user_id)
                .join(TemplateSection, TemplateSection.id == SectionEditRequest.template_section_id)
                .where(
                    SectionEditRequest.report_id == report.id,
                    TemplateSection.section_key == section_key,
                    SectionEditRequest.status == "pending",
                    SectionEditRequest.expired_at.is_(None),
                    SectionEditRequest.requester_user_id != actor.id,
                )
            )
            return [(row[0], row[1], row[2]) for row in rows]

    async def decide(
        self, public_id: str, request_id: UUID, profile: User, approve: bool, reason: str
    ) -> None:
        async with self.manager.session() as session, session.begin():
            report = await lock_report(session, public_id)
            actor = await _actor(session, profile.email)
            if actor.id not in await _approvers(session, report.id):
                raise PermissionError("Only this report's Director or Partner can decide requests.")
            request = await session.get(SectionEditRequest, request_id)
            if request is None or request.report_id != report.id:
                raise ValueError("Request not found in this report.")
            if request.requester_user_id == actor.id:
                raise PermissionError("You cannot approve your own request.")
            if request.status != "pending" or request.expired_at is not None:
                raise ValueError("This request is no longer pending.")
            requester = await session.get(ApplicationUser, request.requester_user_id)
            if requester is None or not requester.active:
                raise ValueError("The requesting user is no longer active.")
            request.status = "approved" if approve else "rejected"
            request.decided_by_user_id = actor.id
            request.decided_at = utc_now()
            request.decision_reason = reason.strip()
            section = await session.get(TemplateSection, request.template_section_id)
            assert section is not None
            _notify(
                session,
                report,
                request.requester_user_id,
                f"Fixed-wording access to {section.title}: {request.status}. "
                "Approval applies only to this report and expires on submission.",
            )
            report.last_activity_at = utc_now()
