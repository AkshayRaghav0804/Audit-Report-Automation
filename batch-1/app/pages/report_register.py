"""Searchable, access-scoped report register and approval queue."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, cast
from urllib.parse import urlencode

from nicegui import app, ui
from sqlalchemy.exc import SQLAlchemyError

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.report_tables import recent_reports_table
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.services.dashboard_service import REPORT_VIEWS, DashboardService, report_view_key
from app.services.display_preferences_service import report_page_size

LOGGER = logging.getLogger(__name__)


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_report_register_routes() -> None:
    for route, title in (("/reports", "Reports"), ("/tasks", "Tasks & Approvals")):
        _register_page(route, title)


def _register_page(route: str, title: str) -> None:
    @ui.page(route, title=f"{title} | QC Report Automation", response_timeout=10)
    async def register_page(
        view: str | None = None,
        stage: str | None = None,
        search: str | None = None,
        page: int = 1,
    ) -> None:
        initial_view = view or ("pending" if route == "/tasks" else "all")
        query = {"view": initial_view, "page": str(page)}
        if stage:
            query["stage"] = stage
        if search:
            query["search"] = search
        user = await require_authentication(f"{route}?{urlencode(query)}")
        if user is None:
            return
        add_application_head()
        notifications = await NotificationState.for_user(user.email, _storage())
        drawer = render_sidebar(user, route)
        render_header(user, title, notifications, drawer)
        with ui.element("main").classes("app-main report-register-page"):
            ui.label(title).classes("welcome-title")
            ui.label(
                "Search reports available to you and follow their progress."
                if route == "/reports"
                else "Open a report to review, approve, or return it for changes."
            ).classes("welcome-subtitle")
            try:
                selected_view = report_view_key(initial_view)
                result = await DashboardService().list_reports(
                    user,
                    view=selected_view,
                    stage=stage,
                    search=search,
                    page=page,
                    page_size=report_page_size(_storage()),
                )
            except (ValueError, PermissionError) as error:
                ui.label(str(error)).classes("text-negative")
                ui.link("Clear filters", route).classes("section-link")
                return
            except (SQLAlchemyError, RuntimeError):
                LOGGER.exception("Unable to load report register")
                ui.label("Reports are temporarily unavailable. Please try again.").classes(
                    "text-negative"
                )
                ui.button("Retry", on_click=lambda: ui.navigate.to(f"{route}?{urlencode(query)}"))
                return

            def navigate(target_page: int = 1) -> None:
                values = {"view": str(view_select.value), "page": str(target_page)}
                if stage_select.value:
                    values["stage"] = str(stage_select.value)
                if search_input.value and search_input.value.strip():
                    values["search"] = search_input.value.strip()
                ui.navigate.to(f"{route}?{urlencode(values)}")

            with (
                ui.element("section")
                .classes("register-filters")
                .props('aria-label="Report filters"')
            ):
                search_input = (
                    ui.input(
                        "Search reports",
                        value=search or "",
                        placeholder="Report ID, title, client or engagement",
                    )
                    .props("outlined clearable")
                    .classes("register-search")
                )
                search_input.on("keydown.enter", lambda: navigate())
                view_select = ui.select(
                    REPORT_VIEWS, value=selected_view, label="Report view"
                ).props("outlined")
                stage_select = ui.select(
                    {"": "All stages", **{name: name for name in result.stages}},
                    value=stage or "",
                    label="Workflow stage",
                ).props("outlined")
                ui.button("Apply filters", icon="filter_list", on_click=lambda: navigate()).props(
                    "unelevated no-caps"
                )
                ui.link("Clear filters", route).classes("section-link")

            with ui.row().classes("register-results-heading items-center justify-between"):
                ui.label(f"{result.total} report{'s' if result.total != 1 else ''}").classes(
                    "section-title"
                ).mark("report-result-count")
                ui.label(REPORT_VIEWS[selected_view] + (f" · {stage}" if stage else "")).classes(
                    "section-subtitle"
                )
            if result.reports:
                with ui.element("section").classes("dashboard-section"):
                    recent_reports_table(result.reports, user, basic_actions_only=True)
            else:
                with ui.column().classes("empty-state items-center"):
                    ui.icon("search_off")
                    ui.label("No reports match these filters").classes("empty-state__title")
                    ui.label("Try another search or clear the filters.").classes(
                        "empty-state__message"
                    )
            with ui.row().classes("register-pagination items-center justify-between"):
                first = (result.page - 1) * result.page_size + 1 if result.total else 0
                last = min(result.page * result.page_size, result.total)
                ui.label(f"Showing {first}–{last} of {result.total}")
                with ui.row().classes("items-center"):
                    previous = ui.button(
                        "Previous", on_click=lambda: navigate(result.page - 1)
                    ).props("flat no-caps")
                    previous.set_enabled(result.page > 1)
                    ui.label(f"Page {result.page} of {result.page_count}")
                    following = ui.button("Next", on_click=lambda: navigate(result.page + 1)).props(
                        "flat no-caps"
                    )
                    following.set_enabled(result.page < result.page_count)
