"""Managed report-category and basic-detail behaviour."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

STATUTORY_REPORT = "Statutory Report"
LIMITED_REVIEW = "Limited Review"
REPORT_CATEGORIES = (STATUTORY_REPORT, LIMITED_REVIEW)

AS_IGAAP = "AS (IGAAP)"
IND_AS = "Ind AS"

HIDDEN_BASIC_FIELDS = frozenset(
    {
        "type_of_entity",
        "type_of_audit",
        "cfs_applicable",
        "type_of_opinion",
        "place_of_signature",
        "firm_name",
        "firm_registration_number",
        "udin_required",
    }
)

CFS_DETAIL_FIELDS = frozenset(
    {
        "type_of_group_structure",
        "number_of_subsidiaries",
        "number_of_associates",
        "number_of_joint_ventures",
        "whether_components_audited_by_other_auditors",
        "number_of_components_audited_by_other_auditors",
    }
)

MANAGED_FRAMEWORK_WORD_FIELDS = frozenset(
    {
        "word_report_vii_p009_e01",
        "word_report_vii_p023_e02",
    }
)


def report_category_for_template(template_key: str, template_name: str) -> str:
    """Classify today's seven audit templates while allowing future limited-review ones."""

    identity = f"{template_key} {template_name}".casefold()
    return LIMITED_REVIEW if "limited review" in identity else STATUTORY_REPORT


def audit_type_answer(category: str) -> str:
    return "Limited Review" if category == LIMITED_REVIEW else "Statutory Audit"


def is_bank_template(template_key: str, template_name: str = "") -> bool:
    return "bank" in f"{template_key} {template_name}".casefold()


def is_consolidated_template(template_key: str, template_name: str = "") -> bool:
    return "consolidated" in f"{template_key} {template_name}".casefold()


def framework_options(template_key: str, template_name: str = "") -> tuple[str, ...]:
    if is_bank_template(template_key, template_name):
        return (AS_IGAAP,)
    return (IND_AS, AS_IGAAP)


def financial_year_options(today: date | None = None) -> tuple[str, ...]:
    """Return recent Indian financial years, newest first, including the next FY."""

    current = today or date.today()
    start_year = current.year if current.month >= 4 else current.year - 1
    return tuple(
        f"FY {year % 100:02d}-{(year + 1) % 100:02d}"
        for year in range(start_year + 1, start_year - 9, -1)
    )


def normalized_framework(value: Any) -> str:
    normalized = str(value or "").strip().casefold().replace(" ", "")
    if normalized in {"as", "igaap", "as(igaap)"}:
        return AS_IGAAP
    if normalized in {"indas", "indianaccountingstandards"}:
        return IND_AS
    return str(value or "").strip()


def managed_report_answers(
    template_key: str,
    template_name: str,
    answers: dict[str, Any],
) -> dict[str, Any]:
    """Apply template-controlled defaults and framework clauses to an answer snapshot."""

    result = dict(answers)
    if is_bank_template(template_key, template_name):
        result["applicable_financial_reporting_framework"] = AS_IGAAP
    framework = normalized_framework(result.get("applicable_financial_reporting_framework"))
    if template_key == "report-vii" and framework:
        if framework == IND_AS:
            result["word_report_vii_p009_e01"] = (
                "with the Indian Accounting Standards prescribed under section 133 of the Act "
                "read with the Companies (Indian Accounting Standards) Rules, 2015, as amended "
                "(‘Ind AS’)"
            )
            result["word_report_vii_p023_e02"] = (
                "Indian Accounting Standards prescribed under section 133 of the Act read with "
                "the Companies (Indian Accounting Standards) Rules, 2015, as amended, "
            )
        elif framework == AS_IGAAP:
            result["word_report_vii_p009_e01"] = (
                "with the Accounting Standards prescribed under section 133 of the Act read "
                "with the Companies (Accounting Standards) Rules, 2021, as amended (‘AS’)"
            )
            result["word_report_vii_p023_e02"] = (
                "Accounting Standards prescribed under section 133 of the Act read with the "
                "Companies (Accounting Standards) Rules, 2021, as amended, "
            )
    return result


def report_text_for_answers(text: str, answers: dict[str, Any]) -> str:
    """Render framework and financial-year choices into controlled report wording."""

    result = text
    framework = normalized_framework(answers.get("applicable_financial_reporting_framework"))
    replacements: tuple[tuple[str, str], ...]
    if framework == AS_IGAAP:
        replacements = (
            (
                "Indian Accounting Standards prescribed under section 133 of the Act read with "
                "the Companies (Indian Accounting Standards) Rules, 2015, as amended, (‘Ind AS’)",
                "Accounting Standards prescribed under section 133 of the Act read with the "
                "Companies (Accounting Standards) Rules, 2021, as amended (‘AS’)",
            ),
            (
                "Indian Accounting Standards prescribed under section 133 of the Act read with "
                "the Companies (Indian Accounting Standards) Rules, 2015, as amended",
                "Accounting Standards prescribed under section 133 of the Act read with the "
                "Companies (Accounting Standards) Rules, 2021, as amended",
            ),
            (
                "the Ind AS specified under Section 133",
                "the Accounting Standards specified under Section 133",
            ),
            ("applicable law or Ind AS", "applicable law or AS"),
        )
    elif framework == IND_AS:
        replacements = (
            (
                "Accounting Standards prescribed under section 133 of the Act read with the "
                "Companies (Accounting Standards) Rules, 2021, as amended (‘AS’)",
                "Indian Accounting Standards prescribed under section 133 of the Act read with "
                "the Companies (Indian Accounting Standards) Rules, 2015, as amended (‘Ind AS’)",
            ),
            (
                "Accounting Standards prescribed under Section 133 of the Act read with the "
                "Companies (Accounting Standards) Rules, 2021, as amended (‘AS’)",
                "Indian Accounting Standards prescribed under Section 133 of the Act read with "
                "the Companies (Indian Accounting Standards) Rules, 2015, as amended (‘Ind AS’)",
            ),
            (
                "Accounting Standards specified under section 133 of the Act read with the "
                "Companies (Accounting Standards) Rules, 2021",
                "Indian Accounting Standards prescribed under section 133 of the Act read with "
                "the Companies (Indian Accounting Standards) Rules, 2015, as amended",
            ),
            (
                "the Accounting Standards specified under Section 133",
                "the Ind AS specified under Section 133",
            ),
        )
    else:
        replacements = ()
    for source, replacement in replacements:
        result = result.replace(source, replacement)

    financial_year = str(answers.get("financial_year") or "").strip()
    match = re.fullmatch(r"FY\s+(\d{2})-(\d{2})", financial_year, flags=re.IGNORECASE)
    if match:
        end_year = 2000 + int(match.group(2))
        result = re.sub(r"31 March 20\d{2}", f"31 March {end_year}", result)
    return result
