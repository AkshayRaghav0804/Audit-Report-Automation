"""Dashboard metric card component."""

from __future__ import annotations

from urllib.parse import urlencode

from nicegui import ui

from app.models.report import DashboardMetric
from app.services.dashboard_service import report_view_key


def metric_card(metric: DashboardMetric) -> None:
    def selected() -> None:
        ui.navigate.to(f"/reports?{urlencode({'view': report_view_key(metric.label)})}")

    button = (
        ui.button(on_click=selected)
        .props(f'flat no-caps aria-label="View {metric.label}"')
        .classes("metric-card")
        .mark("metric-" + report_view_key(metric.label))
    )
    if metric.value is None:
        button.disable()
    with button:
        with ui.row().classes("metric-card__top items-center justify-between"):
            ui.icon(metric.icon).classes(f"metric-card__icon accent-{metric.accent}")
            ui.icon("arrow_forward").classes("metric-card__arrow")
        ui.label(metric.label).classes("metric-card__label")
        ui.label("—" if metric.value is None else str(metric.value)).classes("metric-card__value")
        ui.label(metric.note).classes("metric-card__note")
