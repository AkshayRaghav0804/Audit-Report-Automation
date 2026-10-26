"""Report and dashboard presentation models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Report:
    report_id: str
    name: str
    client: str
    report_type: str
    status: str
    last_updated: datetime
    owner: str


@dataclass(frozen=True, slots=True)
class AttentionItem:
    report_id: str
    report: str
    client: str
    received_from: str
    required_action: str
    due_date: date | None
    current_stage: str
    priority: str
    permitted_actions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkflowStage:
    name: str
    count: int
    icon: str


@dataclass(frozen=True, slots=True)
class DashboardMetric:
    label: str
    value: int | None
    note: str
    icon: str
    accent: str


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    metrics: tuple[DashboardMetric, ...]
    attention_items: tuple[AttentionItem, ...]
    recent_reports: tuple[Report, ...]
    workflow_stages: tuple[WorkflowStage, ...]
    generated_at: datetime


@dataclass(frozen=True, slots=True)
class ReportPage:
    reports: tuple[Report, ...]
    total: int
    page: int
    page_size: int
    stages: tuple[str, ...]

    @property
    def page_count(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)
