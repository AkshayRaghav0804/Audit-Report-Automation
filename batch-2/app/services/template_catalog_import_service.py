"""Import source-backed report definitions into immutable database versions."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.enums import PublicationStatus
from app.database.models.template import (
    QuestionDefinition,
    ReportTemplate,
    ReportTemplateVersion,
    TemplateQuestion,
    TemplateSection,
)
from app.database.session import DatabaseManager, database
from app.models.report_question import ReportQuestion
from app.models.report_template import ReportTemplate as SourceReportTemplate
from app.models.user import User
from app.services.authorization_service import require_permission
from app.services.report_definition_service import get_report_definition_service
from app.services.workflow_service import WorkflowService
from app.source_config import ReportSourceConfig

SOURCE_PARSER_VERSION = 3


@dataclass(frozen=True, slots=True)
class TemplateImportResult:
    template_count: int
    new_version_count: int
    question_count: int


class TemplateCatalogImportService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def import_current_sources(self, profile: User | None = None) -> TemplateImportResult:
        definitions = get_report_definition_service().load()
        config = ReportSourceConfig.from_environment()
        new_versions = 0
        async with self.manager.session() as session:
            async with session.begin():
                if profile is not None:
                    await require_permission(session, profile, "template.manage")
                for source_template in definitions.templates:
                    created = await _import_template(
                        session,
                        source_template,
                        definitions.questions_for(source_template.id),
                        config.template_path.parent,
                    )
                    new_versions += int(created)
        await WorkflowService(self.manager).ensure_default_workflow()
        return TemplateImportResult(
            template_count=len(definitions.templates),
            new_version_count=new_versions,
            question_count=len(definitions.questions),
        )


async def _import_template(
    session: AsyncSession,
    source_template: SourceReportTemplate,
    questions: tuple[ReportQuestion, ...],
    template_directory: Path,
) -> bool:
    template = await session.scalar(
        select(ReportTemplate).where(ReportTemplate.key == source_template.id)
    )
    if template is None:
        template = ReportTemplate(
            key=source_template.id,
            name=source_template.name,
            active=True,
        )
        session.add(template)
        await session.flush()
    else:
        template.name = source_template.name
        template.active = True

    source_path = template_directory / source_template.source_file
    source_payload = source_path.read_bytes()
    source_sha256 = hashlib.sha256(
        source_payload + f"\0source-parser:{SOURCE_PARSER_VERSION}".encode()
    ).hexdigest()
    existing_version = await session.scalar(
        select(ReportTemplateVersion).where(
            ReportTemplateVersion.template_id == template.id,
            ReportTemplateVersion.source_sha256 == source_sha256,
        )
    )
    if existing_version is not None:
        return False

    active_versions = tuple(
        await session.scalars(
            select(ReportTemplateVersion).where(
                ReportTemplateVersion.template_id == template.id,
                ReportTemplateVersion.status == PublicationStatus.ACTIVE,
            )
        )
    )
    active_version_ids = tuple(version.id for version in active_versions)
    previous_defaults = {
        section_key: default_locked
        for section_key, default_locked in await session.execute(
            select(TemplateSection.section_key, TemplateSection.default_locked).where(
                TemplateSection.template_version_id.in_(active_version_ids)
            )
        )
    }
    for active_version in active_versions:
        active_version.status = PublicationStatus.RETIRED
    next_version = (
        await session.scalar(
            select(func.max(ReportTemplateVersion.version_number)).where(
                ReportTemplateVersion.template_id == template.id
            )
        )
        or 0
    ) + 1
    version = ReportTemplateVersion(
        template_id=template.id,
        version_number=next_version,
        status=PublicationStatus.ACTIVE,
        source_filename=source_template.source_file,
        source_sha256=source_sha256,
        formatting_json={
            "source_order": source_template.source_order,
            "source_parser_version": SOURCE_PARSER_VERSION,
        },
    )
    session.add(version)
    await session.flush()

    section_ids: dict[str, UUID] = {}
    for section in source_template.sections:
        model = TemplateSection(
            template_version_id=version.id,
            section_key=section.id,
            title=section.heading,
            display_order=section.order,
            section_type=section.section_type,
            fixed_content=section.content,
            editable=False,
            protected=True,
            default_locked=previous_defaults.get(section.id, True),
            formatting_json={
                "source_paragraph_count": len(section.paragraphs),
                "heading_field_name": section.heading_field_name,
            },
            visibility_rules_json={
                paragraph.id: {
                    "field": paragraph.condition_field,
                    "value": paragraph.condition_value,
                    "default": paragraph.condition_default,
                    "instruction": paragraph.instruction,
                }
                for paragraph in section.paragraphs
                if paragraph.condition_field
            },
        )
        session.add(model)
        await session.flush()
        section_ids[section.id] = model.id

    for question in questions:
        definition = await session.scalar(
            select(QuestionDefinition).where(QuestionDefinition.key == question.field_name)
        )
        if definition is None:
            definition = QuestionDefinition(
                key=question.field_name,
                label=question.label,
                help_text=question.help_text,
                input_type=question.field_type,
                options_json=list(question.options),
                validation_rules_json={},
            )
            session.add(definition)
            await session.flush()
        session.add(
            TemplateQuestion(
                template_version_id=version.id,
                question_id=definition.id,
                section_id=section_ids.get(question.section_id or ""),
                display_order=question.order,
                required=question.required,
                default_value_json=question.default_value,
                conditional_rules_json={},
                mapping_rule_json={"source_question_id": question.id},
                active=True,
            )
        )
    await session.flush()
    return True
