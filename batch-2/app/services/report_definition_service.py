"""Coordinated, validated access to Word templates and Excel questions."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models.report_question import ReportQuestion, UnmappedQuestion
from app.models.report_template import (
    PartnerReference,
    ReportContentSegment,
    ReportSection,
    ReportTemplate,
)
from app.services.document_template_service import DocumentTemplateService
from app.services.question_service import QuestionLoadResult, QuestionService
from app.services.source_errors import SourceDataError
from app.source_config import ReportSourceConfig

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReportDefinitions:
    templates: tuple[ReportTemplate, ...]
    partners: tuple[PartnerReference, ...]
    questions: tuple[ReportQuestion, ...]
    unmapped_rows: tuple[UnmappedQuestion, ...]
    common_question_count: int

    def template(self, template_id: str) -> ReportTemplate | None:
        return next((item for item in self.templates if item.id == template_id), None)

    def questions_for(self, template_id: str) -> tuple[ReportQuestion, ...]:
        return tuple(
            question for question in self.questions if question.report_id in {None, template_id}
        )


class ReportDefinitionService:
    def __init__(
        self,
        template_service: DocumentTemplateService,
        question_service: QuestionService,
    ) -> None:
        self.template_service = template_service
        self.question_service = question_service
        self._cached_templates: tuple[ReportTemplate, ...] | None = None
        self._cached_question_result: QuestionLoadResult | None = None
        self._cached_definitions: ReportDefinitions | None = None

    @classmethod
    def from_config(cls, config: ReportSourceConfig) -> ReportDefinitionService:
        return cls(
            DocumentTemplateService(config.template_path),
            QuestionService(config.question_path),
        )

    def load(self) -> ReportDefinitions:
        templates = self.template_service.load_templates()
        if len(templates) != 7 or len({template.id for template in templates}) != 7:
            raise SourceDataError("Report definitions must contain seven unique template IDs.")
        result = self.question_service.load_questions(templates)
        if (
            templates is self._cached_templates
            and result is self._cached_question_result
            and self._cached_definitions is not None
        ):
            return self._cached_definitions

        partners = self.template_service.load_partners()
        source_questions = _source_colored_text_questions(
            templates,
            existing_field_names={question.field_name for question in result.questions},
        )
        questions = (*result.questions, *source_questions)
        if len({question.id for question in questions}) != len(questions):
            raise SourceDataError("Question definitions contain duplicate IDs.")
        known_section_ids = {section.id for template in templates for section in template.sections}
        invalid_sections = {
            question.section_id
            for question in questions
            if question.section_id and question.section_id not in known_section_ids
        }
        if invalid_sections:
            raise SourceDataError(
                "Question definitions reference unknown section IDs: "
                + ", ".join(sorted(invalid_sections))
            )
        definitions = ReportDefinitions(
            templates=templates,
            partners=partners,
            questions=questions,
            unmapped_rows=result.unmapped_rows,
            common_question_count=result.common_question_count,
        )
        mapped_count = sum(question.report_id is not None for question in questions)
        LOGGER.info(
            "Report templates detected: %d | Questions loaded: %d | Common questions: %d | "
            "Mapped questions: %d | Unmapped questions: %d",
            len(templates),
            len(questions),
            result.common_question_count,
            mapped_count,
            len(result.unmapped_rows),
        )
        if result.common_question_count == len(result.questions):
            LOGGER.warning(
                "No report identifier column was found; all %d questions use the documented "
                "common-question fallback.",
                len(result.questions),
            )
        self._cached_templates = templates
        self._cached_question_result = result
        self._cached_definitions = definitions
        return definitions


def _source_colored_text_questions(
    templates: tuple[ReportTemplate, ...],
    *,
    existing_field_names: set[str],
) -> tuple[ReportQuestion, ...]:
    questions: list[ReportQuestion] = []
    for template in templates:
        seen_fields: set[str] = set(existing_field_names)
        display_order = 10_000
        for section in template.sections:
            editable_number = 0
            if section.heading_field_name and section.heading_field_name not in seen_fields:
                editable_number += 1
                questions.append(
                    _editable_source_question(
                        template,
                        section,
                        ReportContentSegment(
                            section.heading,
                            role="editable",
                            field_name=section.heading_field_name,
                        ),
                        display_order,
                        editable_number,
                        source_row=section.order,
                    )
                )
                seen_fields.add(section.heading_field_name)
                display_order += 1

            for paragraph in section.paragraphs:
                paragraph_segments = (
                    tuple(segment for cell in paragraph.table.cells for segment in cell.segments)
                    if paragraph.table is not None
                    else paragraph.segments
                )
                for segment in paragraph_segments:
                    if (
                        segment.role != "editable"
                        or not segment.field_name
                        or segment.field_name in seen_fields
                    ):
                        continue
                    editable_number += 1
                    questions.append(
                        _editable_source_question(
                            template,
                            section,
                            segment,
                            display_order,
                            editable_number,
                            source_row=paragraph.order,
                        )
                    )
                    seen_fields.add(segment.field_name)
                    display_order += 1

                if (
                    paragraph.table is not None
                    and paragraph.table.repeatable_field_name
                    and paragraph.table.repeatable_field_name not in seen_fields
                ):
                    table_field = paragraph.table.repeatable_field_name
                    questions.append(
                        ReportQuestion(
                            id=f"source-{table_field.replace('_', '-')}",
                            report_id=template.id,
                            field_name=table_field,
                            label=f"{section.heading}: table entries",
                            field_type="table",
                            required=False,
                            options=(),
                            default_value=[],
                            help_text=(
                                "Add, remove, and edit rows using the same columns as the "
                                "controlled Word report."
                            ),
                            order=display_order,
                            section_id=section.id,
                            source_sheet=f"Word: {template.source_file}",
                            source_row=paragraph.order,
                        )
                    )
                    seen_fields.add(table_field)
                    display_order += 1

                if not paragraph.condition_field or paragraph.condition_field in seen_fields:
                    continue
                data_received = paragraph.condition_field.startswith("word_data_received_")
                label = (
                    "Was the other information or report data received?"
                    if data_received
                    else f"Include case-specific wording in {section.heading}?"
                )
                questions.append(
                    ReportQuestion(
                        id=f"source-{paragraph.condition_field.replace('_', '-')}",
                        report_id=template.id,
                        field_name=paragraph.condition_field,
                        label=label,
                        field_type="yes_no",
                        required=False,
                        options=("Yes", "No"),
                        default_value=paragraph.condition_default or "Yes",
                        help_text=paragraph.instruction,
                        order=display_order,
                        section_id=section.id,
                        source_sheet=f"Word: {template.source_file}",
                        source_row=paragraph.order,
                    )
                )
                seen_fields.add(paragraph.condition_field)
                display_order += 1
    return tuple(questions)


def _editable_source_question(
    template: ReportTemplate,
    section: ReportSection,
    segment: ReportContentSegment,
    order: int,
    editable_number: int,
    *,
    source_row: int,
) -> ReportQuestion:
    assert segment.field_name is not None
    preview = " ".join(segment.text.split()).strip("[] ")
    field_type, options = _source_editable_control(preview, segment.text)
    if len(preview) <= 70 and "\n" not in segment.text:
        label = f"{section.heading}: {preview}"
    else:
        label = f"{section.heading}: editable wording {editable_number}"
    return ReportQuestion(
        id=f"source-{segment.field_name.replace('_', '-')}",
        report_id=template.id,
        field_name=segment.field_name,
        label=label,
        field_type=field_type,
        required=False,
        options=options,
        default_value=segment.text,
        help_text="Blue text from the controlled Word report. Edit it for this report.",
        order=order,
        section_id=section.id,
        source_sheet=f"Word: {template.source_file}",
        source_row=source_row,
    )


def _source_editable_control(preview: str, source_text: str) -> tuple[str, tuple[str, ...]]:
    """Choose a compact source-aware control for editable Word wording."""

    if "\n" in source_text or len(preview) > 90:
        return "textarea", ()
    slash_options = tuple(
        option.strip() for option in re.split(r"\s*/\s*", preview) if option.strip()
    )
    if 2 <= len(slash_options) <= 4 and all(len(option) <= 60 for option in slash_options):
        default_option = source_text.strip()
        return "select", tuple(dict.fromkeys((default_option, *slash_options)))
    return "text", ()


_SERVICE = ReportDefinitionService.from_config(ReportSourceConfig.from_environment())


def get_report_definition_service() -> ReportDefinitionService:
    return _SERVICE
