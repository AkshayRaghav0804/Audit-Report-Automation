"""Shared answer-completeness policy for report submission and approval."""

from __future__ import annotations

from typing import Any

from app.database.models.enums import AnswerRequirement

# Temporary workflow setting: unanswered report questions do not block submission
# or approval transitions. Set this back to True when completeness enforcement is
# ready to be restored.
ENFORCE_ANSWER_COMPLETENESS = False

# These answers establish the report identity, reporting decisions, and sign-off details
# needed for a reviewer to perform a meaningful first review. Source-defined required
# flags remain additive when the controlled question workbook starts providing them.
REVIEW_REQUIRED_FIELDS = frozenset(
    {
        "name_of_entity",
        "financial_year",
        "applicable_financial_reporting_framework",
        "eom_applicable",
        "other_matter_paragraph_applicable",
        "key_audit_matters_applicable",
        "caro_applicable",
        "internal_financial_controls_reporting",
        "report_date",
        "partner_name",
        "membership_number",
        "udin",
    }
)


def is_review_required(field_name: str, *, source_required: bool = False) -> bool:
    """Return whether a question blocks a required-only review submission."""

    return source_required or field_name in REVIEW_REQUIRED_FIELDS


def is_applicable(field_name: str, answers: dict[str, Any]) -> bool:
    """Apply the small set of dependencies currently represented by the questions."""

    if field_name == "number_of_components_audited_by_other_auditors":
        return _affirmative(answers.get("whether_components_audited_by_other_auditors"))
    return True


def should_validate(
    field_name: str,
    *,
    source_required: bool,
    requirement: AnswerRequirement,
    answers: dict[str, Any],
) -> bool:
    """Return whether an unanswered field blocks the configured approval stage."""

    if not ENFORCE_ANSWER_COMPLETENESS:
        return False
    if not is_applicable(field_name, answers):
        return False
    if requirement is AnswerRequirement.ALL_APPLICABLE:
        return True
    return is_review_required(field_name, source_required=source_required)


def has_answer(value: Any) -> bool:
    """Treat blank strings and empty selections as unanswered."""

    return value is not None and value != "" and value != []


def _affirmative(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {
            "yes",
            "true",
            "1",
            "applicable",
            "required",
        }
    return bool(value == 1)
