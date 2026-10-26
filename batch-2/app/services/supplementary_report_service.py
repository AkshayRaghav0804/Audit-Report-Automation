"""Conditional report workspaces which accompany the main audit report.

The approved CARO and IFCR source documents are intentionally not modelled as
uploads.  They are report slots selected by the answers on the main report.  A
future source-template integration can populate these slots without changing
the question or navigation contract introduced here.
"""

from __future__ import annotations

from typing import Any

AUDIT_REPORT = "audit_report"
CARO_REPORT = "caro_report"
IFCR_REPORT = "ifcr_report"

REPORT_DOCUMENT_LABELS = {
    AUDIT_REPORT: "Audit report",
    CARO_REPORT: "CARO report",
    IFCR_REPORT: "IFCR report",
}


def available_report_documents(answers: dict[str, Any]) -> tuple[str, ...]:
    """Return the report workspaces enabled by the current basic answers."""

    documents = [AUDIT_REPORT]
    if _affirmative(answers.get("caro_applicable")):
        documents.append(CARO_REPORT)
    if _affirmative(answers.get("internal_financial_controls_reporting")):
        documents.append(IFCR_REPORT)
    return tuple(documents)


def supplementary_report_for_question(
    field_name: str,
    answers: dict[str, Any],
) -> str | None:
    """Map an affirmative conditional question to its report workspace."""

    if not _affirmative(answers.get(field_name)):
        return None
    if field_name == "caro_applicable":
        return CARO_REPORT
    if field_name == "internal_financial_controls_reporting":
        return IFCR_REPORT
    return None


def _affirmative(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in {
        "yes",
        "true",
        "1",
        "applicable",
        "required",
    }
