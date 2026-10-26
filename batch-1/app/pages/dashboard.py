"""Role-based QC Report Automation dashboard."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from datetime import datetime
from typing import Any, cast
from urllib.parse import urlencode

from nicegui import app, ui
from sqlalchemy.exc import SQLAlchemyError

from app.components.header import add_application_head, render_header
from app.components.metric_card import metric_card
from app.components.notification_menu import NotificationState
from app.components.report_dialog import create_report_dialog
from app.components.report_tables import attention_table, recent_reports_table
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.models.report import DashboardSnapshot
from app.models.user import User
from app.services.dashboard_service import DashboardService, unavailable_dashboard_snapshot
from app.services.permission_service import has_permission, is_report_creator, is_report_reviewer

LOGGER = logging.getLogger(__name__)
DASHBOARD_RESPONSE_TIMEOUT_SECONDS = 10.0


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_dashboard_routes() -> None:
    @ui.page(
        "/dashboard",
        title="Dashboard | QC Report Automation",
        response_timeout=DASHBOARD_RESPONSE_TIMEOUT_SECONDS,
    )
    async def dashboard_page(action: str | None = None) -> None:
        return_path = "/dashboard" + (f"?{urlencode({'action': action})}" if action else "")
        user = await require_authentication(return_path)
        if user is None:
            return

        add_application_head()
        notification_state = await NotificationState.for_user(user.email, _storage())
        drawer = render_sidebar(user, "/dashboard")
        render_header(user, "Dashboard", notification_state, drawer)
        dialog = (
            create_report_dialog(user, _storage())
            if has_permission(user, "report.create")
            else None
        )
        dashboard_error = False
        try:
            snapshot = await DashboardService().load(user)
        except (SQLAlchemyError, RuntimeError, ValueError) as error:
            LOGGER.exception("Unable to load the database-backed dashboard: %s", error)
            snapshot = unavailable_dashboard_snapshot()
            dashboard_error = True

        with ui.element("main").classes("app-main dashboard-page"):
            with ui.row().classes("welcome-section items-start justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label(f"{_greeting()}, {user.name.split()[0]}").classes("welcome-title")
                    ui.label(_dashboard_intro(user)).classes("welcome-subtitle")
                    ui.label(_updated_label(snapshot)).classes("dashboard-updated-at")
                with ui.row().classes("dashboard-heading-actions items-center"):
                    ui.button(
                        "Refresh",
                        icon="refresh",
                        on_click=lambda: ui.navigate.to("/dashboard"),
                    ).props("flat no-caps").classes("dashboard-refresh-button")
                    if has_permission(user, "report.create"):
                        assert dialog is not None
                        ui.button("Create New Report", icon="add", on_click=dialog.open).props(
                            "unelevated no-caps"
                        ).classes("create-report-button")

            if dashboard_error:
                with ui.row().classes("dashboard-data-error items-start no-wrap"):
                    ui.icon("cloud_off")
                    with ui.column().classes("gap-0"):
                        ui.label("Dashboard data is temporarily unavailable").classes(
                            "dashboard-data-error__title"
                        )
                        ui.label(
                            "No sample values are being shown. Check the database connection "
                            "and applied migrations, then retry."
                        ).classes("dashboard-data-error__message")

            with ui.element("section").classes("metrics-grid").props('aria-label="Report metrics"'):
                for metric in snapshot.metrics:
                    metric_card(metric)

            with ui.element("section").classes("dashboard-section attention-section"):
                combined_role = is_report_creator(user) and is_report_reviewer(user)
                _section_heading(
                    (
                        "Requires Your Attention"
                        if combined_role
                        else "My Approval Queue"
                        if is_report_reviewer(user)
                        else "Requires Your Attention"
                    ),
                    (
                        "Your returned reports and active approval assignments"
                        if combined_role
                        else "Reports currently assigned to your active approval stage"
                        if is_report_reviewer(user)
                        else "Your returned reports and other actions requiring attention"
                    ),
                    "View all reports"
                    if combined_role
                    else "View all tasks"
                    if is_report_reviewer(user)
                    else "View all reports",
                    "/reports"
                    if combined_role
                    else "/tasks"
                    if is_report_reviewer(user)
                    else "/reports",
                )
                attention_table(snapshot.attention_items, user)

            with ui.element("section").classes("dashboard-section"):
                _section_heading(
                    "My Reports and Reviews"
                    if combined_role
                    else "My Review History"
                    if is_report_reviewer(user)
                    else "My Reports",
                    (
                        "Reports you created and review assignments available to you"
                        if combined_role
                        else "Active assignments and completed reviews available to you"
                        if is_report_reviewer(user)
                        else "Reports you created, including drafts and submitted work"
                    ),
                    "View all reports",
                    "/reports",
                )
                recent_reports_table(snapshot.recent_reports, user)

            with ui.element("section").classes("dashboard-section workflow-section"):
                _section_heading(
                    "Workflow Overview",
                    "Current report volume at each approval stage",
                )
                with ui.element("div").classes("workflow-pipeline"):
                    for index, stage in enumerate(snapshot.workflow_stages):
                        _workflow_card(stage.name, stage.count, stage.icon)
                        if index < len(snapshot.workflow_stages) - 1:
                            ui.icon("chevron_right").classes("workflow-connector")

        if action == "create" and dialog is not None:
            await dialog.open()


def _dashboard_intro(user: User) -> str:
    if is_report_creator(user) and is_report_reviewer(user):
        return "Track reports you created and approvals assigned to your email in one place."
    if is_report_reviewer(user):
        return "Here are the reports currently assigned to you and your completed reviews."
    if is_report_creator(user):
        return "Track the reports you created, your drafts, and their approval status."
    return "Here is an overview of reports and actions requiring your attention."


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"


def _updated_label(snapshot: DashboardSnapshot) -> str:
    return f"Updated {snapshot.generated_at.astimezone():%d %b %Y, %I:%M %p}"


def _section_heading(
    title: str,
    subtitle: str,
    link_label: str | None = None,
    link_target: str | None = None,
) -> None:
    with ui.row().classes("section-heading items-end justify-between"):
        with ui.column().classes("gap-0"):
            ui.label(title).classes("section-title")
            ui.label(subtitle).classes("section-subtitle")
        if link_label and link_target:
            with ui.link(target=link_target).classes("section-link"):
                ui.label(link_label)
                ui.icon("arrow_forward")


def _workflow_card(name: str, count: int, icon: str) -> None:
    def selected() -> None:
        ui.navigate.to(f"/reports?{urlencode({'stage': name})}")

    with (
        ui.button(on_click=selected)
        .props(f'flat no-caps aria-label="View reports in {name}"')
        .classes("workflow-card")
    ):
        ui.icon(icon).classes("workflow-card__icon")
        with ui.column().classes("workflow-card__copy gap-0"):
            ui.label(str(count)).classes("workflow-card__count")
            ui.label(name).classes("workflow-card__label")
