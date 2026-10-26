"""Administrative defaults copied into section locks when a report is created."""

from dataclasses import dataclass

from sqlalchemy import select

from app.database.models.enums import PublicationStatus
from app.database.models.template import ReportTemplate, ReportTemplateVersion, TemplateSection
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.report_access_service import _actor_has_role
from app.services.report_collaboration_service import _actor


@dataclass(frozen=True, slots=True)
class TemplateSectionLockDefault:
    template_key: str
    template_name: str
    version_number: int
    section_key: str
    section_title: str
    display_order: int
    locked: bool


class TemplateSectionLockService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def active_defaults(self, profile: User) -> tuple[TemplateSectionLockDefault, ...]:
        async with self.manager.session() as session:
            actor = await _actor(session, profile.email)
            if not await _actor_has_role(session, actor.id, "administrator"):
                raise PermissionError("Only administrators can manage template section defaults.")
            rows = await session.execute(
                select(
                    ReportTemplate.key,
                    ReportTemplate.name,
                    ReportTemplateVersion.version_number,
                    TemplateSection.section_key,
                    TemplateSection.title,
                    TemplateSection.display_order,
                    TemplateSection.default_locked,
                )
                .join(
                    ReportTemplateVersion,
                    ReportTemplateVersion.template_id == ReportTemplate.id,
                )
                .join(
                    TemplateSection,
                    TemplateSection.template_version_id == ReportTemplateVersion.id,
                )
                .where(
                    ReportTemplate.active.is_(True),
                    ReportTemplateVersion.status == PublicationStatus.ACTIVE,
                )
                .order_by(ReportTemplate.name, TemplateSection.display_order)
            )
            return tuple(TemplateSectionLockDefault(*row) for row in rows)

    async def set_default(
        self,
        template_key: str,
        section_key: str,
        profile: User,
        locked: bool,
    ) -> None:
        async with self.manager.session() as session, session.begin():
            actor = await _actor(session, profile.email)
            if not await _actor_has_role(session, actor.id, "administrator"):
                raise PermissionError("Only administrators can manage template section defaults.")
            section = await session.scalar(
                select(TemplateSection)
                .join(
                    ReportTemplateVersion,
                    ReportTemplateVersion.id == TemplateSection.template_version_id,
                )
                .join(
                    ReportTemplate,
                    ReportTemplate.id == ReportTemplateVersion.template_id,
                )
                .where(
                    ReportTemplate.key == template_key,
                    ReportTemplate.active.is_(True),
                    ReportTemplateVersion.status == PublicationStatus.ACTIVE,
                    TemplateSection.section_key == section_key,
                )
                .with_for_update()
            )
            if section is None:
                raise ValueError("The selected template section is no longer active.")
            section.default_locked = locked
