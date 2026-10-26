"""Administrative view of persisted application audit history."""

from __future__ import annotations

from collections import Counter
from collections.abc import MutableMapping
from datetime import UTC, timedelta, timezone
from typing import Any, cast

from nicegui import app, ui
from sqlalchemy.exc import SQLAlchemyError

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.services.audit_history_service import (
    AUDIT_PERMISSION,
    DEFAULT_AUDIT_LIMIT,
    AuditHistoryEvent,
    AuditHistoryService,
)
from app.services.permission_service import has_permission

ROUTE = "/admin/audit-logs"
INDIA_TIMEZONE = timezone(timedelta(hours=5, minutes=30), name="Asia/Kolkata")


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_admin_audit_history_routes() -> None:
    @ui.page(ROUTE, title="Audit History | QC Report Automation")
    async def admin_audit_history_page() -> None:
        user = await require_authentication(ROUTE)
        if user is None:
            return

        add_application_head()
        ui.add_head_html('<link rel="stylesheet" href="/static/styles/audit_history.css?v=1">')
        drawer = render_sidebar(user, ROUTE)
        render_header(
            user,
            "Audit History",
            await NotificationState.for_user(user.email, _storage()),
            drawer,
        )
        if not has_permission(user, AUDIT_PERMISSION):
            _render_access_denied()
            return

        try:
            events = await AuditHistoryService().load(user)
        except (OSError, RuntimeError, SQLAlchemyError):
            _render_unavailable()
            return
        _render_history(events)


def _render_history(events: tuple[AuditHistoryEvent, ...]) -> None:
    category_counts = Counter(event.category for event in events)
    with ui.element("main").classes("app-main"):
        with ui.row().classes("w-full items-start justify-between gap-4"):
            with ui.column().classes("gap-1"):
                ui.label("Audit History").classes("welcome-title").mark("audit-history-title")
                ui.label(
                    "Real application activity from the database, newest first. "
                    f"Showing up to the latest {DEFAULT_AUDIT_LIMIT} events."
                ).classes("welcome-subtitle")
            ui.button(
                "Refresh",
                icon="refresh",
                on_click=lambda: ui.navigate.to(ROUTE),
            ).props("outline no-caps").mark("audit-history-refresh")

        with ui.row().classes("w-full gap-4 q-mt-lg"):
            _metric("Events loaded", len(events), "history")
            _metric("Approval events", category_counts["Approval"], "approval")
            _metric(
                "Content changes",
                category_counts["Report content"] + category_counts["Controlled wording"],
                "edit_note",
            )
            _metric("Master-data imports", category_counts["Master data"], "upload_file")

        with ui.card().classes("w-full q-mt-lg"):
            with ui.row().classes("w-full items-center justify-between gap-3"):
                with ui.column().classes("gap-0"):
                    ui.label("Recorded activity").classes("section-title")
                    ui.label(
                        "Search by actor, report, action, field, reason, or correlation ID."
                    ).classes("section-subtitle")
                search = (
                    ui.input(placeholder="Search audit history")
                    .props('outlined dense clearable aria-label="Search audit history"')
                    .classes("w-80")
                    .mark("audit-history-search")
                )

            if not events:
                with ui.column().classes("w-full items-center text-center q-pa-xl"):
                    ui.icon("history_toggle_off").classes("placeholder-page__icon")
                    ui.label("No audit events recorded yet").classes("section-title")
                    ui.label(
                        "Report creation, content changes, approvals, and imports will appear here."
                    ).classes("section-subtitle")
                return

            table = (
                ui.table(
                    rows=[_event_row(event) for event in events],
                    columns=_columns(),
                    row_key="id",
                    pagination={"rowsPerPage": 25},
                )
                .classes("w-full audit-history-table")
                .mark("audit-history-table")
            )
            table.bind_filter_from(search, "value")


def _metric(label: str, value: int, icon: str) -> None:
    with ui.card().classes("audit-history-metric"):
        with ui.row().classes("items-center no-wrap gap-3"):
            ui.icon(icon).classes("audit-history-metric__icon")
            with ui.column().classes("gap-0"):
                ui.label(str(value)).classes("audit-history-metric__value")
                ui.label(label).classes("audit-history-metric__label")


def _event_row(event: AuditHistoryEvent) -> dict[str, str]:
    occurred = event.occurred_at
    if occurred.tzinfo is None:
        occurred = occurred.replace(tzinfo=UTC)
    occurred = occurred.astimezone(INDIA_TIMEZONE)
    report = "—"
    if event.report_public_id:
        report = event.report_public_id
        if event.report_title:
            report += f" · {event.report_title}"
    actor = event.actor_name
    if event.actor_role:
        actor += f" · {event.actor_role}"
    details = event.details
    if event.correlation_id:
        details += f" Correlation: {event.correlation_id}."
    return {
        "id": event.id,
        "occurred": occurred.strftime("%d %b %Y, %I:%M %p"),
        "occurred_sort": occurred.isoformat(),
        "category": event.category,
        "action": event.action,
        "actor": actor,
        "actor_email": event.actor_email,
        "report": report,
        "subject": event.subject,
        "details": details,
    }


def _columns() -> list[dict[str, Any]]:
    return [
        {
            "name": "occurred",
            "label": "When",
            "field": "occurred",
            "align": "left",
        },
        {"name": "category", "label": "Type", "field": "category", "align": "left"},
        {"name": "action", "label": "Action", "field": "action", "align": "left"},
        {"name": "actor", "label": "Actor", "field": "actor", "align": "left"},
        {"name": "report", "label": "Report", "field": "report", "align": "left"},
        {"name": "subject", "label": "Subject", "field": "subject", "align": "left"},
        {"name": "details", "label": "Details", "field": "details", "align": "left"},
    ]


def _render_access_denied() -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("placeholder-page items-center text-center"):
            ui.icon("block").classes("placeholder-page__icon")
            ui.label("Access denied").classes("welcome-title")
            ui.label("Audit history requires the audit_log.view permission.").classes(
                "welcome-subtitle"
            )


def _render_unavailable() -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("placeholder-page items-center text-center"):
            ui.icon("history_toggle_off").classes("placeholder-page__icon")
            ui.label("Audit history unavailable").classes("welcome-title")
            ui.label(
                "The database could not be read. Refresh the page after connectivity is restored."
            ).classes("welcome-subtitle")
            ui.button("Retry", icon="refresh", on_click=lambda: ui.navigate.to(ROUTE)).props(
                "unelevated no-caps"
            ).classes("primary-action")
