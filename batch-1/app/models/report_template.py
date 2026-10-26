"""Report templates and their source-derived preview sections."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PartnerReference:
    name: str
    icai_membership_number: str


@dataclass(frozen=True, slots=True)
class ReportContentSegment:
    text: str
    role: str = "fixed"
    field_name: str | None = None


@dataclass(frozen=True, slots=True)
class ReportTableCell:
    row: int
    column: int
    segments: tuple[ReportContentSegment, ...]


@dataclass(frozen=True, slots=True)
class ReportTable:
    id: str
    cells: tuple[ReportTableCell, ...]
    row_count: int
    column_count: int
    repeatable_field_name: str | None = None


@dataclass(frozen=True, slots=True)
class ReportNumberingLevel:
    format: str
    text: str
    start: int = 1


@dataclass(frozen=True, slots=True)
class ReportNumbering:
    id: int
    level: int
    levels: tuple[ReportNumberingLevel, ...]
    left_indent_twips: int = 0
    hanging_indent_twips: int = 0
    justification: str = "left"
    suffix: str = "tab"


@dataclass(frozen=True, slots=True)
class ReportParagraph:
    id: str
    segments: tuple[ReportContentSegment, ...]
    order: int
    condition_field: str | None = None
    condition_value: str | None = None
    condition_default: str | None = None
    instruction: str | None = None
    table: ReportTable | None = None
    numbering: ReportNumbering | None = None


@dataclass(frozen=True, slots=True)
class ReportSection:
    id: str
    heading: str
    content: str
    order: int
    section_type: str = "fixed"
    paragraphs: tuple[ReportParagraph, ...] = ()
    heading_field_name: str | None = None


@dataclass(frozen=True, slots=True)
class ReportTemplate:
    id: str
    name: str
    source_file: str
    source_order: int
    sections: tuple[ReportSection, ...]
