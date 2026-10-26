"""Session-local report answers and backend-enforced section overrides."""

from __future__ import annotations

import re
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.models.report_question import ReportQuestion
from app.models.report_template import (
    ReportNumberingLevel,
    ReportParagraph,
    ReportSection,
    ReportTemplate,
)
from app.models.user import User
from app.services.permission_service import has_permission
from app.services.report_definition_service import ReportDefinitionService
from app.services.submission_policy import (
    ENFORCE_ANSWER_COMPLETENESS,
    has_answer,
    is_applicable,
    is_review_required,
)

DRAFTS_KEY = "report_drafts"
SECTION_EDIT_PERMISSION = "report.fixed_section.edit"

WORKFLOW_QUESTION_FIELDS = {
    "partner_name": "workflow_partner",
    "membership_number": "workflow_partner_icai_membership_number",
}

SOURCE_PLACEHOLDERS = {
    "name_of_entity": ("[Name of the Company]", "[Name of the Bank]"),
    "number_of_subsidiaries": ("[Number of Subsidiaries]",),
    "number_of_associates": ("[Number of associates]",),
    "number_of_joint_ventures": ("[Number of joint ventures]",),
    "partner_name": ("[Name of the Partner]",),
    "membership_number": ("[XXXXXX]",),
    "udin": ("[XXXXXXXXXXXXXX]",),
}

WORKFLOW_STAGES = (
    "Preparation",
    "Partner Review",
    "QC Review",
    "EQCR Review",
    "Final Approval",
)
COMPLETE_ANSWER_STAGES = frozenset({"QC Review", "EQCR Review"})


@dataclass(frozen=True, slots=True)
class PreviewSegment:
    text: str
    highlighted: bool = False
    field_name: str | None = None


class DraftSubmissionError(ValueError):
    def __init__(self, target_stage: str, missing_questions: tuple[str, ...]) -> None:
        self.target_stage = target_stage
        self.missing_questions = missing_questions
        super().__init__(
            f"Complete these questions before sending to {target_stage}: "
            + ", ".join(missing_questions)
        )


def report_numbering_labels(
    template: ReportTemplate,
    answers: dict[str, Any],
) -> dict[str, str]:
    """Resolve Word list labels for the paragraphs visible in the current scenario."""

    counters: dict[int, list[int | None]] = {}
    labels: dict[str, str] = {}
    for section in template.sections:
        if not source_section_is_visible(section, answers):
            continue
        for paragraph in section.paragraphs:
            if not source_paragraph_is_visible(paragraph, answers):
                continue
            numbering = paragraph.numbering
            if numbering is None or not numbering.levels:
                continue
            values = counters.setdefault(numbering.id, [])
            while len(values) < len(numbering.levels):
                values.append(None)
            for index in range(numbering.level):
                if values[index] is None:
                    values[index] = numbering.levels[index].start
            current = values[numbering.level]
            values[numbering.level] = (
                numbering.levels[numbering.level].start if current is None else current + 1
            )
            for index in range(numbering.level + 1, len(values)):
                values[index] = None
            labels[paragraph.id] = _format_numbering_label(numbering.levels, values)
    return labels


def _format_numbering_label(
    levels: tuple[ReportNumberingLevel, ...],
    counters: list[int | None],
) -> str:
    template = levels[-1].text

    def replace(match: re.Match[str]) -> str:
        index = int(match.group(1)) - 1
        if index >= len(counters) or counters[index] is None:
            return ""
        level = levels[index] if index < len(levels) else levels[-1]
        counter = counters[index]
        assert counter is not None
        return _format_number(counter, level.format)

    return re.sub(r"%([1-9])", replace, template)


def _format_number(value: int, format_name: str) -> str:
    if format_name == "lowerLetter":
        return _alphabetic_number(value).lower()
    if format_name == "upperLetter":
        return _alphabetic_number(value)
    if format_name == "lowerRoman":
        return _roman_number(value).lower()
    if format_name == "upperRoman":
        return _roman_number(value)
    return str(value)


def _alphabetic_number(value: int) -> str:
    result = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _roman_number(value: int) -> str:
    numerals = (
        (1000, "M"),
        (900, "CM"),
        (500, "D"),
        (400, "CD"),
        (100, "C"),
        (90, "XC"),
        (50, "L"),
        (40, "XL"),
        (10, "X"),
        (9, "IX"),
        (5, "V"),
        (4, "IV"),
        (1, "I"),
    )
    result = ""
    for amount, numeral in numerals:
        count, value = divmod(value, amount)
        result += numeral * count
    return result


def draft_storage_key(template_id: str, report_id: str | None = None) -> str:
    """Return a browser-storage key isolated to one persisted report."""

    normalized_report_id = str(report_id or "").strip()
    return f"{template_id}::{normalized_report_id}" if normalized_report_id else template_id


def answers_for(storage: MutableMapping[str, Any], template_id: str) -> dict[str, Any]:
    draft = _draft(storage, template_id)
    answers = draft.setdefault("answers", {})
    return dict(answers) if isinstance(answers, dict) else {}


def report_answers_for(storage: MutableMapping[str, Any], template_id: str) -> dict[str, Any]:
    """Return draft answers with report questions resolved from workflow metadata."""

    answers = answers_for(storage, template_id)
    for question_field, workflow_field in WORKFLOW_QUESTION_FIELDS.items():
        workflow_value = answers.get(workflow_field)
        if _has_value(workflow_value):
            answers[question_field] = workflow_value
    return answers


def workflow_answered_question_fields(
    storage: MutableMapping[str, Any], template_id: str
) -> frozenset[str]:
    """Identify report questions already answered in the Create Report dialog."""

    answers = answers_for(storage, template_id)
    return frozenset(
        question_field
        for question_field, workflow_field in WORKFLOW_QUESTION_FIELDS.items()
        if _has_value(answers.get(workflow_field))
    )


def workflow_stage_for(storage: MutableMapping[str, Any], template_id: str) -> str:
    stage = _draft(storage, template_id).get("workflow_stage")
    return str(stage) if stage in WORKFLOW_STAGES else WORKFLOW_STAGES[0]


def next_workflow_stage_for(storage: MutableMapping[str, Any], template_id: str) -> str | None:
    current_stage = workflow_stage_for(storage, template_id)
    current_index = WORKFLOW_STAGES.index(current_stage)
    return WORKFLOW_STAGES[current_index + 1] if current_index + 1 < len(WORKFLOW_STAGES) else None


def missing_questions_for_submission(
    storage: MutableMapping[str, Any],
    template_id: str,
    questions: tuple[ReportQuestion, ...],
) -> tuple[str, ...]:
    if not ENFORCE_ANSWER_COMPLETENESS:
        return ()
    target_stage = next_workflow_stage_for(storage, template_id)
    if target_stage is None:
        return ()
    answers = report_answers_for(storage, template_id)
    if target_stage in COMPLETE_ANSWER_STAGES:
        questions_to_check = tuple(
            question for question in questions if is_applicable(question.field_name, answers)
        )
    else:
        questions_to_check = tuple(
            question
            for question in questions
            if is_review_required(
                question.field_name,
                source_required=question.required,
            )
            and is_applicable(question.field_name, answers)
        )
    return tuple(
        question.label
        for question in questions_to_check
        if not has_answer(answers.get(question.field_name, question.default_value))
    )


def submit_for_approval(
    storage: MutableMapping[str, Any],
    template_id: str,
    questions: tuple[ReportQuestion, ...],
) -> str:
    target_stage = next_workflow_stage_for(storage, template_id)
    if target_stage is None:
        raise ValueError("This report has already reached final approval.")
    missing_questions = missing_questions_for_submission(storage, template_id, questions)
    if missing_questions:
        raise DraftSubmissionError(target_stage, missing_questions)
    _draft(storage, template_id)["workflow_stage"] = target_stage
    _persist_drafts(storage)
    return target_stage


def save_answer(
    storage: MutableMapping[str, Any], template_id: str, field_name: str, value: Any
) -> None:
    draft = _draft(storage, template_id)
    answers = draft.setdefault("answers", {})
    if not isinstance(answers, dict):
        answers = {}
        draft["answers"] = answers
    answers[field_name] = value
    _persist_drafts(storage)


def section_overrides_for(storage: MutableMapping[str, Any], template_id: str) -> dict[str, str]:
    overrides = _draft(storage, template_id).setdefault("section_overrides", {})
    return dict(overrides) if isinstance(overrides, dict) else {}


def set_section_override(
    storage: MutableMapping[str, Any], template_id: str, section_id: str, content: str
) -> None:
    draft = _draft(storage, template_id)
    overrides = draft.setdefault("section_overrides", {})
    if not isinstance(overrides, dict):
        overrides = {}
        draft["section_overrides"] = overrides
    overrides[section_id] = content
    _persist_drafts(storage)


def update_section_override(
    storage: MutableMapping[str, Any],
    user: User,
    definitions: ReportDefinitionService,
    template_id: str,
    section_id: str,
    content: str,
) -> None:
    if not has_permission(user, SECTION_EDIT_PERMISSION):
        raise PermissionError("You do not have permission to edit fixed report sections.")
    template = definitions.load().template(template_id)
    if template is None or not any(section.id == section_id for section in template.sections):
        raise ValueError("The requested report section does not exist.")
    set_section_override(storage, template_id, section_id, content)


def substitute_placeholders(
    content: str,
    answers: dict[str, Any],
    *,
    placeholder_texts: Mapping[str, str] | None = None,
) -> str:
    """Replace explicit app placeholders and exact source-document placeholders."""

    return "".join(
        segment.text
        for segment in placeholder_segments(
            content,
            answers,
            placeholder_texts=placeholder_texts,
        )
    )


def link_section_override_fields(section: ReportSection, content: str) -> str:
    """Keep source-controlled editable wording linked inside a section override."""

    if content == section.content:
        paragraphs: list[str] = []
        for paragraph in section.paragraphs:
            if (
                paragraph.condition_field is not None
                and paragraph.condition_default != paragraph.condition_value
            ):
                continue
            text = _linked_paragraph_text(paragraph)
            if text:
                paragraphs.append(text)
        return "\n\n".join(paragraphs).strip() or content

    linked = content
    cursor = 0
    for paragraph in section.paragraphs:
        for segment in paragraph.segments:
            if segment.role != "editable" or not segment.field_name or not segment.text:
                continue
            placeholder = f"{{{{{segment.field_name}}}}}"
            if placeholder in linked:
                cursor = linked.index(placeholder, cursor) + len(placeholder)
                continue
            position = linked.find(segment.text, cursor)
            if position < 0:
                continue
            if len(segment.text.strip()) < 3 and linked.count(segment.text, cursor) > 1:
                continue
            linked = linked[:position] + placeholder + linked[position + len(segment.text) :]
            cursor = position + len(placeholder)
    return linked


def section_placeholder_texts(section: ReportSection) -> dict[str, str]:
    """Return the exact source-template wording represented by each linked field."""

    texts: dict[str, str] = {}
    if section.heading_field_name:
        texts[section.heading_field_name] = section.heading
    for paragraph in section.paragraphs:
        for segment in paragraph.segments:
            if segment.role == "editable" and segment.field_name and segment.text:
                texts.setdefault(segment.field_name, segment.text)
        if paragraph.table is None:
            continue
        for cell in paragraph.table.cells:
            for segment in cell.segments:
                if segment.role == "editable" and segment.field_name and segment.text:
                    texts.setdefault(segment.field_name, segment.text)
    return texts


def relink_section_editor_content(
    linked_content: str,
    edited_content: str,
    answers: dict[str, Any],
    *,
    placeholder_texts: Mapping[str, str] | None = None,
) -> str:
    """Restore placeholders whose displayed answer text was left unchanged in the editor."""

    placeholder_pattern = re.compile(r"\{\{([a-z0-9_]+)\}\}")
    rendered_parts: list[str] = []
    linked_spans: list[tuple[str, int, int]] = []
    position = 0
    rendered_length = 0
    for match in placeholder_pattern.finditer(linked_content):
        fixed_text = linked_content[position : match.start()]
        rendered_parts.append(fixed_text)
        rendered_length += len(fixed_text)
        field_name = match.group(1)
        value = answers.get(field_name)
        if _has_value(value):
            rendered_value = format_answer_for_preview(value)
        else:
            rendered_value = (placeholder_texts or {}).get(field_name, match.group(0))
        start = rendered_length
        rendered_parts.append(rendered_value)
        rendered_length += len(rendered_value)
        linked_spans.append((match.group(0), start, rendered_length))
        position = match.end()
    rendered_parts.append(linked_content[position:])
    rendered_content = "".join(rendered_parts)
    if edited_content == rendered_content:
        return linked_content

    matching_blocks = SequenceMatcher(
        None,
        rendered_content,
        edited_content,
        autojunk=False,
    ).get_matching_blocks()
    replacements: list[tuple[int, int, str]] = []
    for placeholder, start, end in linked_spans:
        for block in matching_blocks:
            if block.a <= start and end <= block.a + block.size:
                edited_start = block.b + start - block.a
                edited_end = edited_start + end - start
                replacements.append((edited_start, edited_end, placeholder))
                break

    relinked = edited_content
    for start, end, placeholder in sorted(replacements, reverse=True):
        relinked = relinked[:start] + placeholder + relinked[end:]
    return relinked


def format_answer_for_preview(value: Any) -> str:
    """Return a faithful, human-readable representation of a report answer."""

    if not _has_value(value):
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return ", ".join(sorted(str(item) for item in value))
    return str(value)


def source_paragraph_is_visible(
    paragraph: ReportParagraph,
    answers: dict[str, Any],
) -> bool:
    """Resolve a green Word instruction against its scenario answer."""

    if not paragraph.condition_field or paragraph.condition_value is None:
        instruction_visible = True
    else:
        value = answers.get(paragraph.condition_field, paragraph.condition_default)
        instruction_visible = (
            str(value or "").strip().casefold() == paragraph.condition_value.casefold()
        )
    if not instruction_visible:
        return False
    text = "".join(segment.text for segment in paragraph.segments).casefold()
    if (
        "companies (auditor" in text
        and ("order, 2020" in text or "caro" in text)
        and not _scenario_enabled(answers.get("caro_applicable"))
    ):
        return False
    if (
        "adequacy of" in text
        and "internal financial controls" in text
        and not _scenario_enabled(answers.get("internal_financial_controls_reporting"))
    ):
        return False
    return True


def source_section_is_visible(section: ReportSection, answers: dict[str, Any]) -> bool:
    """Show optional Word sections according to their matching Excel scenario answer."""

    heading = section.heading.strip().casefold()
    scenario_field = None
    if heading == "emphasis of matter":
        scenario_field = "eom_applicable"
    elif heading == "key audit matters":
        scenario_field = "key_audit_matters_applicable"
    elif heading in {"other matter", "other matters"}:
        scenario_field = "other_matter_paragraph_applicable"
    return scenario_field is None or _scenario_enabled(answers.get(scenario_field))


def basic_questions_complete(
    answers: dict[str, Any], questions: tuple[ReportQuestion, ...]
) -> bool:
    """Return whether every currently applicable Excel question has an answer."""

    return all(
        _has_value(answers.get(question.field_name, question.default_value))
        for question in questions
        if is_applicable(question.field_name, answers)
    )


def placeholder_segments(
    content: str,
    answers: dict[str, Any],
    *,
    placeholder_texts: Mapping[str, str] | None = None,
) -> tuple[PreviewSegment, ...]:
    """Split report text so inserted answer values can be highlighted safely in the UI."""

    source_fields = {
        placeholder: field_name
        for field_name, placeholders in SOURCE_PLACEHOLDERS.items()
        for placeholder in placeholders
    }
    source_pattern = "|".join(
        re.escape(placeholder) for placeholder in sorted(source_fields, key=len, reverse=True)
    )
    pattern = re.compile(r"\{\{([a-z0-9_]+)\}\}" + (f"|{source_pattern}" if source_pattern else ""))
    segments: list[PreviewSegment] = []
    position = 0
    for match in pattern.finditer(content):
        if match.start() > position:
            segments.append(PreviewSegment(content[position : match.start()]))
        field_name = match.group(1) or source_fields.get(match.group(0), "")
        value = answers.get(field_name)
        if _has_value(value):
            segments.append(
                PreviewSegment(
                    format_answer_for_preview(value),
                    highlighted=True,
                    field_name=field_name,
                )
            )
        else:
            segments.append(
                PreviewSegment(
                    (placeholder_texts or {}).get(field_name, match.group(0)),
                    highlighted=True,
                    field_name=field_name,
                )
            )
        position = match.end()
    if position < len(content):
        segments.append(PreviewSegment(content[position:]))
    return tuple(segments) or (PreviewSegment(content),)


def _linked_paragraph_text(paragraph: ReportParagraph) -> str:
    parts = (
        f"{{{{{segment.field_name}}}}}"
        if segment.role == "editable" and segment.field_name
        else segment.text
        for segment in paragraph.segments
        if segment.role != "guidance"
    )
    return " ".join("".join(parts).split())


def _draft(storage: MutableMapping[str, Any], template_id: str) -> dict[str, Any]:
    drafts = storage.setdefault(DRAFTS_KEY, {})
    if not isinstance(drafts, dict):
        drafts = {}
        storage[DRAFTS_KEY] = drafts
    draft = drafts.setdefault(template_id, {"answers": {}, "section_overrides": {}})
    if not isinstance(draft, dict):
        draft = {"answers": {}, "section_overrides": {}}
        drafts[template_id] = draft
    return draft


def _persist_drafts(storage: MutableMapping[str, Any]) -> None:
    """Trigger persistence for stores that do not observe nested dictionary changes."""

    storage[DRAFTS_KEY] = storage.get(DRAFTS_KEY, {})


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, frozenset)):
        return bool(value)
    return True


def _scenario_enabled(value: Any) -> bool:
    """Keep optional wording visible until a user explicitly answers the scenario."""

    if not _has_value(value):
        return True
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"yes", "true", "1", "applicable", "required"}
