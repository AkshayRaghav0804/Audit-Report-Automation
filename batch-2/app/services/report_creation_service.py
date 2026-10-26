"""Database-backed report creation and workflow-assignee validation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select

from app.config import Settings
from app.database.models.actor import ApplicationUser, ProfessionalCredential, Role, UserRole
from app.database.models.enums import PublicationStatus, RevisionStatus, StageType
from app.database.models.report import (
    Report,
    ReportRevision,
    ReportSignatory,
    ReportWorkflowAssignment,
)
from app.database.models.section_access import ReportSectionLock
from app.database.models.template import (
    ReportTemplate,
    ReportTemplateVersion,
    TemplateQuestion,
    TemplateSection,
)
from app.database.models.workflow import (
    ApprovalStepDefinition,
    ApprovalWorkflowVersion,
    ReportTemplateWorkflow,
)
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.schemas.directory import DirectoryPerson
from app.services.directory_service import DirectoryService
from app.services.report_definition_service import get_report_definition_service
from app.services.template_catalog_import_service import TemplateCatalogImportService
from app.services.workflow_service import validate_report_assignments


@dataclass(frozen=True, slots=True)
class ReportTemplateChoice:
    id: UUID
    key: str
    name: str
    version_number: int
    question_count: int
    section_count: int


@dataclass(frozen=True, slots=True)
class WorkflowStageChoice:
    id: UUID
    stage_code: str
    stage_type: StageType
    display_name: str
    sequence: int
    required_role_key: str
    min_assignees: int
    max_assignees: int | None

    @property
    def optional(self) -> bool:
        return self.min_assignees == 0

    @property
    def multiple(self) -> bool:
        return self.max_assignees is None or self.max_assignees > 1


@dataclass(frozen=True, slots=True)
class ReportCreationContext:
    preparer: DirectoryPerson
    templates: tuple[ReportTemplateChoice, ...]
    stages_by_template: dict[UUID, tuple[WorkflowStageChoice, ...]]
    all_people: tuple[DirectoryPerson, ...]
    people_by_role: dict[str, tuple[DirectoryPerson, ...]]


@dataclass(frozen=True, slots=True)
class NewReportRequest:
    template_id: UUID
    engagement_code: str | None
    creator_email: str
    assignments: dict[str, tuple[UUID, ...]]


@dataclass(frozen=True, slots=True)
class CreatedReport:
    id: UUID
    public_id: str
    template_key: str


class ReportCreationService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def context_for(self, profile: User) -> ReportCreationContext:
        directory = DirectoryService(self.manager)
        preparer = await directory.ensure_profile_person(profile)
        await TemplateCatalogImportService(self.manager).import_current_sources()
        await directory.ensure_source_partners(get_report_definition_service().load().partners)
        async with self.manager.session() as session:
            template_rows = tuple(
                await session.execute(
                    select(ReportTemplate, ReportTemplateVersion)
                    .join(
                        ReportTemplateVersion,
                        ReportTemplateVersion.template_id == ReportTemplate.id,
                    )
                    .where(
                        ReportTemplate.active.is_(True),
                        ReportTemplateVersion.status == PublicationStatus.ACTIVE,
                    )
                    .order_by(ReportTemplate.name)
                )
            )
            templates: list[ReportTemplateChoice] = []
            stages_by_template: dict[UUID, tuple[WorkflowStageChoice, ...]] = {}
            role_keys: set[str] = set()
            for template, version in template_rows:
                question_count = await session.scalar(
                    select(func.count(TemplateQuestion.id)).where(
                        TemplateQuestion.template_version_id == version.id,
                        TemplateQuestion.active.is_(True),
                    )
                )
                section_count = await session.scalar(
                    select(func.count(TemplateSection.id)).where(
                        TemplateSection.template_version_id == version.id
                    )
                )
                templates.append(
                    ReportTemplateChoice(
                        id=template.id,
                        key=template.key,
                        name=template.name,
                        version_number=version.version_number,
                        question_count=int(question_count or 0),
                        section_count=int(section_count or 0),
                    )
                )
                steps = await _active_steps(session, template.id)
                stages = tuple(_stage_choice(step) for step in steps)
                stages_by_template[template.id] = stages
                role_keys.update(
                    stage.required_role_key
                    for stage in stages
                    if stage.stage_type not in {StageType.MANAGER, StageType.DIRECTOR}
                )

        all_people = await directory.list_people(limit=None)
        people_by_role = {
            role_key: await directory.list_people(role_key=role_key, limit=None)
            for role_key in sorted(role_keys)
        }
        return ReportCreationContext(
            preparer=preparer,
            templates=tuple(templates),
            stages_by_template=stages_by_template,
            all_people=all_people,
            people_by_role=people_by_role,
        )

    async def create(self, request: NewReportRequest) -> CreatedReport:
        engagement_code = (request.engagement_code or "").strip()
        if len(engagement_code) > 100:
            raise ValueError("Engagement code cannot exceed 100 characters.")
        async with self.manager.session() as session:
            async with session.begin():
                creator = await session.scalar(
                    select(ApplicationUser).where(
                        ApplicationUser.email == request.creator_email.casefold(),
                        ApplicationUser.active.is_(True),
                    )
                )
                if creator is None:
                    raise ValueError("The current user is not active in the people directory.")
                creator_roles = set(
                    await session.scalars(
                        select(Role.key)
                        .join(UserRole, UserRole.role_id == Role.id)
                        .where(UserRole.user_id == creator.id, Role.active.is_(True))
                    )
                )
                # Explicit local demo role switching retains its accumulated maker role.
                # Connected reviewer identities cannot inherit creation from Employee.
                if "administrator" not in creator_roles and (
                    not creator_roles.intersection({"employee", "manager"})
                    or (
                        creator_roles.intersection({"director", "partner", "qc", "eqcr"})
                        and not Settings.from_environment().demo_auth_enabled
                    )
                ):
                    raise PermissionError("Only an Employee or Manager can create a report.")
                template_row = (
                    await session.execute(
                        select(ReportTemplate, ReportTemplateVersion)
                        .join(
                            ReportTemplateVersion,
                            ReportTemplateVersion.template_id == ReportTemplate.id,
                        )
                        .where(
                            ReportTemplate.id == request.template_id,
                            ReportTemplate.active.is_(True),
                            ReportTemplateVersion.status == PublicationStatus.ACTIVE,
                        )
                    )
                ).one_or_none()
                if template_row is None:
                    raise ValueError("The selected report template is no longer active.")
                template, template_version = template_row
                workflow_version = await _active_workflow_version(session, template.id)
                if workflow_version is None:
                    raise ValueError("No active approval workflow is assigned to this template.")
                steps = await _active_steps(session, template.id)
                validate_report_assignments(
                    steps,
                    request.assignments,
                    preparer_user_id=creator.id,
                )
                people = await _validated_people(session, steps, request.assignments)

                report = Report(
                    public_id=f"RPT-{datetime.now().year}-{uuid4().hex[:8].upper()}",
                    template_version_id=template_version.id,
                    workflow_version_id=workflow_version.id,
                    title=template.name,
                    engagement_code=engagement_code or None,
                    created_by_user_id=creator.id,
                    owner_user_id=creator.id,
                )
                session.add(report)
                await session.flush()
                session.add(
                    ReportRevision(
                        report_id=report.id,
                        revision_number=1,
                        status=RevisionStatus.EDITABLE,
                        created_by_user_id=creator.id,
                    )
                )
                section_defaults = await session.execute(
                    select(TemplateSection.id, TemplateSection.default_locked).where(
                        TemplateSection.template_version_id == template_version.id
                    )
                )
                session.add_all(
                    ReportSectionLock(
                        report_id=report.id,
                        template_section_id=section_id,
                        locked=default_locked,
                        changed_by_user_id=creator.id,
                    )
                    for section_id, default_locked in section_defaults
                )
                for step in steps:
                    for order, user_id in enumerate(
                        request.assignments.get(step.stage_code, ()), start=1
                    ):
                        person = people[user_id]
                        session.add(
                            ReportWorkflowAssignment(
                                report_id=report.id,
                                step_definition_id=step.id,
                                user_id=user_id,
                                assignment_order=order,
                                role_key_snapshot=step.required_role_key,
                                display_name_snapshot=person.display_name,
                                email_snapshot=person.email,
                                selected_by_user_id=creator.id,
                            )
                        )
                        if step.stage_type is StageType.PARTNER:
                            credential = await session.scalar(
                                select(ProfessionalCredential).where(
                                    ProfessionalCredential.user_id == user_id,
                                    ProfessionalCredential.professional_body == "ICAI",
                                    ProfessionalCredential.active.is_(True),
                                )
                            )
                            if credential is None:
                                raise ValueError(
                                    f"{person.display_name} has no active ICAI membership number."
                                )
                            session.add(
                                ReportSignatory(
                                    report_id=report.id,
                                    user_id=user_id,
                                    display_name_snapshot=person.display_name,
                                    professional_body_snapshot="ICAI",
                                    membership_number_snapshot=credential.membership_number,
                                    assigned_by_user_id=creator.id,
                                )
                            )
                await session.flush()
                return CreatedReport(
                    id=report.id,
                    public_id=report.public_id,
                    template_key=template.key,
                )


async def _active_workflow_version(
    session: object, template_id: UUID
) -> ApprovalWorkflowVersion | None:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    return cast(
        ApprovalWorkflowVersion | None,
        await session.scalar(
            select(ApprovalWorkflowVersion)
            .join(
                ReportTemplateWorkflow,
                ReportTemplateWorkflow.workflow_id == ApprovalWorkflowVersion.workflow_id,
            )
            .where(
                ReportTemplateWorkflow.template_id == template_id,
                ReportTemplateWorkflow.is_default.is_(True),
                ApprovalWorkflowVersion.status == PublicationStatus.ACTIVE,
            )
            .order_by(ApprovalWorkflowVersion.version_number.desc())
        ),
    )


async def _active_steps(session: object, template_id: UUID) -> tuple[ApprovalStepDefinition, ...]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    workflow_version = await _active_workflow_version(session, template_id)
    if workflow_version is None:
        return ()
    return tuple(
        await session.scalars(
            select(ApprovalStepDefinition)
            .where(ApprovalStepDefinition.workflow_version_id == workflow_version.id)
            .order_by(ApprovalStepDefinition.sequence)
        )
    )


async def _validated_people(
    session: object,
    steps: tuple[ApprovalStepDefinition, ...],
    assignments: dict[str, tuple[UUID, ...]],
) -> dict[UUID, ApplicationUser]:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    result: dict[UUID, ApplicationUser] = {}
    for step in steps:
        for user_id in assignments.get(step.stage_code, ()):
            person = await session.scalar(
                select(ApplicationUser).where(
                    ApplicationUser.id == user_id,
                    ApplicationUser.active.is_(True),
                )
            )
            if person is None:
                raise ValueError(
                    f"A selected {step.display_name} assignee is inactive or unavailable."
                )
            if step.stage_type not in {StageType.MANAGER, StageType.DIRECTOR}:
                has_required_role = await session.scalar(
                    select(UserRole.id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(
                        UserRole.user_id == user_id,
                        Role.key == step.required_role_key,
                        Role.active.is_(True),
                    )
                )
                if has_required_role is None:
                    raise ValueError(
                        f"{person.display_name} is not available for {step.display_name}."
                    )
            result[user_id] = person
    return result


def _stage_choice(step: ApprovalStepDefinition) -> WorkflowStageChoice:
    return WorkflowStageChoice(
        id=step.id,
        stage_code=step.stage_code,
        stage_type=step.stage_type,
        display_name=step.display_name,
        sequence=step.sequence,
        required_role_key=step.required_role_key,
        min_assignees=step.min_assignees,
        max_assignees=step.max_assignees,
    )
