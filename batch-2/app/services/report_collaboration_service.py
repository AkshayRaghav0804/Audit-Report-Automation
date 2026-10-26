"""Persistent fixed-section edits and section comments for assigned reviewers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select

from app.database.models.actor import ApplicationUser
from app.database.models.approval import ReportApprovalStage
from app.database.models.mixins import utc_now
from app.database.models.report import (
    Report,
    ReportComment,
    ReportRevision,
    ReportSectionChange,
    ReportSectionContent,
    ReportSectionHeading,
)
from app.database.models.template import (
    ReportTemplate,
    ReportTemplateVersion,
    TemplateSection,
)
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.notification_service import notify_comment_added
from app.services.report_access_service import ReportAccess, ReportAccessService
from app.services.report_edit_role_service import report_edit_role

MAX_SECTION_CONTENT_LENGTH = 250_000
MAX_COMMENT_LENGTH = 10_000


@dataclass(frozen=True, slots=True)
class SectionComment:
    author: str
    body: str
    created_at: datetime
    selected_text: str | None = None
    selection_start: int | None = None


@dataclass(frozen=True, slots=True)
class SectionChangeSummary:
    section_key: str
    actor_label: str
    actor_role: str
    stage_label: str
    reason: str
    previous_content: str
    new_content: str
    changed_at: datetime
    target: str = "content"


class ReportCollaborationService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager
        self.access = ReportAccessService(manager)

    async def _resolved_access(
        self,
        public_id: str,
        profile: User,
        access: ReportAccess | None,
    ) -> ReportAccess:
        if access is None:
            return await self.access.access_for(public_id, profile)
        if access.report_public_id != public_id:
            raise PermissionError("The validated access does not belong to this report.")
        return access

    async def section_overrides(
        self,
        public_id: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> dict[str, str]:
        access = await self._resolved_access(public_id, profile, access)
        if not access.can_view:
            raise PermissionError("You do not have access to this report.")
        async with self.manager.session() as session:
            rows = await session.execute(
                select(TemplateSection.section_key, ReportSectionContent.content)
                .join(
                    ReportSectionContent,
                    ReportSectionContent.template_section_id == TemplateSection.id,
                )
                .join(
                    ReportRevision,
                    ReportRevision.id == ReportSectionContent.report_revision_id,
                )
                .join(Report, Report.id == ReportRevision.report_id)
                .where(
                    Report.public_id == public_id,
                    ReportRevision.revision_number == Report.current_revision_number,
                )
            )
            return {section_key: content for section_key, content in rows}

    async def section_heading_overrides(
        self,
        public_id: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> dict[str, str]:
        access = await self._resolved_access(public_id, profile, access)
        if not access.can_view:
            raise PermissionError("You do not have access to this report.")
        async with self.manager.session() as session:
            rows = await session.execute(
                select(TemplateSection.section_key, ReportSectionHeading.heading)
                .join(
                    ReportSectionHeading,
                    ReportSectionHeading.template_section_id == TemplateSection.id,
                )
                .join(
                    ReportRevision,
                    ReportRevision.id == ReportSectionHeading.report_revision_id,
                )
                .join(Report, Report.id == ReportRevision.report_id)
                .where(
                    Report.public_id == public_id,
                    ReportRevision.revision_number == Report.current_revision_number,
                )
            )
            return {str(section_key): str(heading) for section_key, heading in rows}

    async def save_section(
        self,
        *,
        public_id: str,
        template_key: str,
        section_key: str,
        content: str,
        profile: User,
        reason: str | None = None,
    ) -> None:
        access = await self.access.access_for(public_id, profile)
        if not access.can_edit_fixed_sections:
            raise PermissionError(
                "Only people who can view this in-progress report can edit its sections."
            )
        if not content.strip():
            raise ValueError("Section content cannot be empty.")
        if len(content) > MAX_SECTION_CONTENT_LENGTH:
            raise ValueError("Section content cannot exceed 250,000 characters.")
        normalized_reason = str(reason or "").strip()
        async with self.manager.session() as session:
            async with session.begin():
                actor = await _actor(session, profile.email)
                from app.services.section_access_service import lock_report, section_is_editable

                await lock_report(session, public_id)
                report, revision, section = await _report_revision_section(
                    session, public_id, template_key, section_key
                )
                if not await section_is_editable(session, report.id, section.id, actor.id):
                    raise PermissionError(
                        "This section is locked. Request fixed-wording access from the "
                        "report's Director or Partner. Blue fields remain editable."
                    )
                actor_role = await report_edit_role(session, report, actor.id)
                existing = await session.scalar(
                    select(ReportSectionContent).where(
                        ReportSectionContent.report_revision_id == revision.id,
                        ReportSectionContent.template_section_id == section.id,
                    )
                )
                stage = (
                    await session.get(ReportApprovalStage, access.active_stage_id)
                    if access.active_stage_id is not None
                    else None
                )
                previous_content = (
                    existing.content
                    if existing is not None
                    else section.fixed_content or section.default_content or ""
                )
                if previous_content == content:
                    raise ValueError("The controlled wording has not changed.")
                if existing is None:
                    session.add(
                        ReportSectionContent(
                            report_revision_id=revision.id,
                            template_section_id=section.id,
                            content=content,
                            edited_by_user_id=actor.id,
                        )
                    )
                else:
                    existing.content = content
                    existing.content_version += 1
                    existing.edited_by_user_id = actor.id
                session.add(
                    ReportSectionChange(
                        report_id=report.id,
                        report_revision_id=revision.id,
                        template_section_id=section.id,
                        report_approval_stage_id=stage.id if stage is not None else None,
                        previous_content=previous_content,
                        new_content=content,
                        change_target="content",
                        reason=normalized_reason,
                        changed_by_user_id=actor.id,
                        actor_label_snapshot=(
                            "QC Team" if actor_role == "QC Reviewer" else actor.display_name
                        ),
                        actor_role_snapshot=actor_role,
                        stage_label_snapshot=(
                            stage.display_name_snapshot if stage is not None else "Preparation"
                        ),
                    )
                )
                report.last_activity_at = utc_now()

    async def save_section_heading(
        self,
        *,
        public_id: str,
        template_key: str,
        section_key: str,
        heading: str,
        profile: User,
        reason: str | None = None,
    ) -> None:
        access = await self.access.access_for(public_id, profile)
        if not access.can_edit_fixed_sections:
            raise PermissionError(
                "Only people who can view this in-progress report can edit its headings."
            )
        normalized_heading = " ".join(str(heading).split())
        if not normalized_heading:
            raise ValueError("Section heading cannot be empty.")
        if len(normalized_heading) > 300:
            raise ValueError("Section heading cannot exceed 300 characters.")
        normalized_reason = str(reason or "").strip()
        async with self.manager.session() as session:
            async with session.begin():
                actor = await _actor(session, profile.email)
                from app.services.section_access_service import lock_report, section_is_editable

                await lock_report(session, public_id)
                report, revision, section = await _report_revision_section(
                    session, public_id, template_key, section_key
                )
                if not await section_is_editable(session, report.id, section.id, actor.id):
                    raise PermissionError(
                        "This section is locked. Request fixed-wording access from the "
                        "report's Director or Partner. Blue fields remain editable."
                    )
                actor_role = await report_edit_role(session, report, actor.id)
                existing = await session.scalar(
                    select(ReportSectionHeading).where(
                        ReportSectionHeading.report_revision_id == revision.id,
                        ReportSectionHeading.template_section_id == section.id,
                    )
                )
                stage = (
                    await session.get(ReportApprovalStage, access.active_stage_id)
                    if access.active_stage_id is not None
                    else None
                )
                previous_heading = existing.heading if existing is not None else section.title
                if previous_heading == normalized_heading:
                    raise ValueError("The section heading has not changed.")
                if existing is None:
                    session.add(
                        ReportSectionHeading(
                            report_revision_id=revision.id,
                            template_section_id=section.id,
                            heading=normalized_heading,
                            edited_by_user_id=actor.id,
                        )
                    )
                else:
                    existing.heading = normalized_heading
                    existing.heading_version += 1
                    existing.edited_by_user_id = actor.id
                session.add(
                    ReportSectionChange(
                        report_id=report.id,
                        report_revision_id=revision.id,
                        template_section_id=section.id,
                        report_approval_stage_id=stage.id if stage is not None else None,
                        previous_content=previous_heading,
                        new_content=normalized_heading,
                        change_target="heading",
                        reason=normalized_reason,
                        changed_by_user_id=actor.id,
                        actor_label_snapshot=(
                            "QC Team" if actor_role == "QC Reviewer" else actor.display_name
                        ),
                        actor_role_snapshot=actor_role,
                        stage_label_snapshot=(
                            stage.display_name_snapshot if stage is not None else "Preparation"
                        ),
                    )
                )
                report.last_activity_at = utc_now()

    async def section_changes(
        self,
        public_id: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> tuple[SectionChangeSummary, ...]:
        access = await self._resolved_access(public_id, profile, access)
        if not access.can_view:
            raise PermissionError("You do not have access to this report.")
        async with self.manager.session() as session:
            rows = await session.execute(
                select(
                    TemplateSection.section_key,
                    ReportSectionChange.actor_label_snapshot,
                    ReportSectionChange.actor_role_snapshot,
                    ReportSectionChange.stage_label_snapshot,
                    ReportSectionChange.reason,
                    ReportSectionChange.previous_content,
                    ReportSectionChange.new_content,
                    ReportSectionChange.changed_at,
                    ReportSectionChange.change_target,
                )
                .join(
                    ReportSectionChange,
                    ReportSectionChange.template_section_id == TemplateSection.id,
                )
                .join(Report, Report.id == ReportSectionChange.report_id)
                .where(
                    Report.public_id == public_id,
                    ReportSectionChange.change_target.in_(("content", "heading")),
                )
                .order_by(ReportSectionChange.changed_at.desc())
            )
            return tuple(
                SectionChangeSummary(
                    section_key=str(section_key),
                    actor_label=str(actor_label),
                    actor_role=str(actor_role or stage_label),
                    stage_label=str(stage_label),
                    reason=str(reason),
                    previous_content=str(previous_content),
                    new_content=str(new_content),
                    changed_at=changed_at,
                    target=str(change_target),
                )
                for (
                    section_key,
                    actor_label,
                    actor_role,
                    stage_label,
                    reason,
                    previous_content,
                    new_content,
                    changed_at,
                    change_target,
                ) in rows
            )

    async def comments(
        self,
        public_id: str,
        section_key: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> tuple[SectionComment, ...]:
        return (await self.comments_for_report(public_id, profile, access=access)).get(
            section_key, ()
        )

    async def comments_for_report(
        self,
        public_id: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> dict[str, tuple[SectionComment, ...]]:
        """Return every section comment in one query for the inline preview threads."""
        access = await self._resolved_access(public_id, profile, access)
        if not access.can_view:
            raise PermissionError("You do not have access to this report.")
        async with self.manager.session() as session:
            rows = await session.execute(
                select(
                    ReportComment.section_key,
                    ApplicationUser.display_name,
                    ReportApprovalStage.required_role_key_snapshot,
                    ReportComment.body,
                    ReportComment.created_at,
                    ReportComment.selected_text,
                    ReportComment.selection_start,
                )
                .join(ApplicationUser, ApplicationUser.id == ReportComment.author_user_id)
                .outerjoin(
                    ReportApprovalStage,
                    ReportApprovalStage.id == ReportComment.report_approval_stage_id,
                )
                .join(Report, Report.id == ReportComment.report_id)
                .where(Report.public_id == public_id)
                .order_by(ReportComment.created_at)
            )
            comments_by_section: dict[str, list[SectionComment]] = {}
            for (
                section_key,
                author,
                role_key,
                body,
                created,
                selected_text,
                selection_start,
            ) in rows:
                comments_by_section.setdefault(str(section_key), []).append(
                    SectionComment(
                        "QC Team" if role_key == "qc" else str(author),
                        str(body),
                        created,
                        str(selected_text) if selected_text is not None else None,
                        int(selection_start) if selection_start is not None else None,
                    )
                )
            return {
                section_key: tuple(comments)
                for section_key, comments in comments_by_section.items()
            }

    async def comment_counts(
        self,
        public_id: str,
        profile: User,
        *,
        access: ReportAccess | None = None,
    ) -> dict[str, int]:
        """Return section-level comment totals for preview highlighting."""
        access = await self._resolved_access(public_id, profile, access)
        if not access.can_view:
            raise PermissionError("You do not have access to this report.")
        async with self.manager.session() as session:
            rows = await session.execute(
                select(ReportComment.section_key, func.count(ReportComment.id))
                .join(Report, Report.id == ReportComment.report_id)
                .where(Report.public_id == public_id)
                .group_by(ReportComment.section_key)
            )
            return {str(section_key): int(count) for section_key, count in rows}

    async def add_comment(
        self,
        *,
        public_id: str,
        section_key: str,
        body: str,
        profile: User,
        selected_text: str | None = None,
        selection_start: int | None = None,
    ) -> None:
        access = await self.access.access_for(public_id, profile)
        if not access.can_comment:
            raise PermissionError(
                "Only the report owner or active assigned reviewer can add report comments."
            )
        normalized = body.strip()
        if not normalized:
            raise ValueError("Comment text is required.")
        if len(normalized) > MAX_COMMENT_LENGTH:
            raise ValueError("Comment text cannot exceed 10,000 characters.")
        normalized_selection = str(selected_text or "")
        if len(normalized_selection) > 2_000:
            raise ValueError("Comment selections cannot exceed 2,000 characters.")
        if selection_start is not None and selection_start < 0:
            raise ValueError("The comment selection position is invalid.")
        if not normalized_selection.strip():
            normalized_selection = ""
            selection_start = None
        async with self.manager.session() as session:
            async with session.begin():
                actor = await _actor(session, profile.email)
                report = await session.scalar(select(Report).where(Report.public_id == public_id))
                if report is None:
                    raise ValueError("Report not found.")
                revision = await session.scalar(
                    select(ReportRevision).where(
                        ReportRevision.report_id == report.id,
                        ReportRevision.revision_number == report.current_revision_number,
                    )
                )
                if revision is None:
                    raise RuntimeError("The report has no current revision.")
                session.add(
                    ReportComment(
                        report_id=report.id,
                        report_revision_id=revision.id,
                        report_approval_stage_id=access.active_stage_id,
                        section_key=section_key,
                        body=normalized,
                        selected_text=normalized_selection or None,
                        selection_start=selection_start,
                        author_user_id=actor.id,
                    )
                )
                stage = (
                    await session.get(ReportApprovalStage, access.active_stage_id)
                    if access.active_stage_id is not None
                    else None
                )
                report.last_activity_at = utc_now()
                await notify_comment_added(
                    session,
                    report,
                    section_key=section_key,
                    actor=actor,
                    actor_label=(
                        "QC Team"
                        if stage is not None
                        and stage.required_role_key_snapshot == "qc"
                        and not access.is_owner
                        else actor.display_name
                    ),
                    stage=stage,
                )


async def _actor(session: object, email: str) -> ApplicationUser:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    actor = await session.scalar(
        select(ApplicationUser).where(
            ApplicationUser.email == email.casefold(), ApplicationUser.active.is_(True)
        )
    )
    if actor is None:
        raise PermissionError("The current user is not active in the people directory.")
    return actor


async def _report_revision_section(
    session: object,
    public_id: str,
    template_key: str,
    section_key: str,
) -> tuple[Report, ReportRevision, TemplateSection]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    row = (
        await session.execute(
            select(Report, ReportRevision, TemplateSection)
            .join(
                ReportRevision,
                ReportRevision.report_id == Report.id,
            )
            .join(
                ReportTemplateVersion,
                ReportTemplateVersion.id == Report.template_version_id,
            )
            .join(ReportTemplate, ReportTemplate.id == ReportTemplateVersion.template_id)
            .join(
                TemplateSection,
                TemplateSection.template_version_id == ReportTemplateVersion.id,
            )
            .where(
                Report.public_id == public_id,
                ReportRevision.revision_number == Report.current_revision_number,
                ReportTemplate.key == template_key,
                TemplateSection.section_key == section_key,
            )
        )
    ).one_or_none()
    if row is None:
        raise ValueError("The requested report section does not exist.")
    report, revision, section = row
    return report, revision, section
