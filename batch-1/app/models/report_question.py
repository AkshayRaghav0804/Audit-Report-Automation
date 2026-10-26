"""Normalized question definitions loaded from the review workbook."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ReportQuestion:
    id: str
    report_id: str | None
    field_name: str
    label: str
    field_type: str
    required: bool
    options: tuple[str, ...]
    default_value: Any | None
    help_text: str | None
    order: int
    section_id: str | None
    source_sheet: str
    source_row: int


@dataclass(frozen=True, slots=True)
class UnmappedQuestion:
    source_sheet: str
    source_row: int
    label: str
    report_reference: str
    reason: str
