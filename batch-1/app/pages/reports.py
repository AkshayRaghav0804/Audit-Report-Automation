"""Placeholder report route prepared for future editor deep links."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, cast
from urllib.parse import quote, urlencode

from nicegui import app, ui

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.services.report_access_service import ReportAccessService


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_report_routes() -> None:
    @ui.page("/reports/{report_id}", title="Report | QC Report Automation")
    async def report_page(
        report_id: str,
        view: str | None = None,
        section: str | None = None,
        comment: str | None = None,
        action: str | None = None,
    ) -> None:
        query_values = {
            key: value
            for key, value in {
                "view": view,
                "section": section,
                "comment": comment,
                "action": action,
            }.items()
            if value is not None
        }
        return_path = f"/reports/{quote(report_id, safe='')}"
        if query_values:
            return_path += f"?{urlencode(query_values)}"
        user = await require_authentication(return_path)
        if user is None:
            return
        if not report_id.strip():
            ui.notify("A valid report ID is required.", type="warning", position="top-right")
            ui.navigate.to("/dashboard")
            return

        add_application_head()
        state = await NotificationState.for_user(user.email, _storage())
        drawer = render_sidebar(user, "/reports")
        render_header(user, "Report", state, drawer)
        try:
            access = await ReportAccessService().access_for(report_id, user)
        except (PermissionError, ValueError) as error:
            _access_denied(str(error))
            return
        if not access.can_view:
            _access_denied("This report is not owned by or assigned to you.")
            return

        # Secure links and dashboard rows should open the real report workspace directly.
        # Access is checked above before the report identifier is passed to the editor.
        ui.navigate.to(
            f"/reports/new/{quote(access.template_key, safe='')}?"
            f"{urlencode({'report_id': report_id})}"
        )
        return

        with ui.element("main").classes("app-main"):
            ui.button(
                "Return to dashboard",
                icon="arrow_back",
                on_click=lambda: ui.navigate.to("/dashboard"),
            ).props("flat no-caps").classes("back-link")
            with ui.row().classes("report-placeholder-heading items-start justify-between"):
                with ui.column().classes("gap-1"):
                    ui.label("Report workspace").classes("welcome-title")
                    ui.label(report_id).classes("report-id-label")
                    ui.label(f"Signed in as {user.name} · {user.role}").classes("cell-supporting")
                ui.label(access.report_status.value.replace("_", " ").title()).classes(
                    "status-badge status-badge--draft"
                )

            with ui.row().classes("context-banner items-start no-wrap"):
                ui.icon("route")
                with ui.column().classes("gap-0"):
                    ui.label(f"Created by {access.creator_name}").classes("context-banner__title")
                    stage_label = access.active_stage or access.report_status.value.title()
                    ui.label(
                        f"Current stage: {stage_label}"
                        + (
                            f" · Received from {access.received_from}"
                            if access.active_stage
                            else ""
                        )
                    ).classes("context-banner__message")

            ui.link(
                "Open report content",
                f"/reports/new/{access.template_key}?report_id={report_id}",
            ).classes("primary-action report-open-content-link")

            if view == "workflow":
                _context_banner(
                    "account_tree",
                    "Workflow view requested",
                    "The approval workflow panel will open here.",
                )
            elif section or comment:
                context_message = (
                    f"Section: {section or 'Not specified'} | Comment: {comment or 'Not specified'}"
                )
                _context_banner("comment", "Comment deep link requested", context_message)
            elif action:
                _context_banner(
                    "play_circle",
                    f"{action.title()} action requested",
                    "The report editor will handle this action.",
                )

            with ui.element("section").classes("editor-placeholder"):
                with ui.row().classes("editor-placeholder__toolbar items-center justify-between"):
                    with ui.column().classes("gap-0"):
                        ui.label("Basis for Opinion").classes("editor-placeholder__title")
                        ui.label("Live preview section example").classes("cell-supporting")
                    with ui.row().classes("items-center gap-1"):
                        if access.can_edit_fixed_sections:
                            with (
                                ui.button(icon="edit")
                                .props('flat round dense aria-label="Edit fixed section"')
                                .classes("preview-action")
                            ):
                                ui.tooltip("Edit section")
                        if access.can_comment or access.is_completed_review:
                            with (
                                ui.button(icon="comment")
                                .props('flat round dense aria-label="Comment on section"')
                                .classes("preview-action")
                            ):
                                ui.tooltip("Comments")
                ui.separator()
                ui.label(
                    "Open the report content to review its questions, Word-derived sections, "
                    "persistent comments, and any actions available at your assigned stage."
                ).classes("editor-placeholder__body")


def _context_banner(icon: str, title: str, message: str) -> None:
    with ui.row().classes("context-banner items-start no-wrap"):
        ui.icon(icon)
        with ui.column().classes("gap-0"):
            ui.label(title).classes("context-banner__title")
            ui.label(message).classes("context-banner__message")


def _access_denied(message: str) -> None:
    with ui.element("main").classes("app-main"):
        with ui.column().classes("page-error items-center text-center"):
            ui.icon("lock")
            ui.label("Report access denied").classes("welcome-title")
            ui.label(message).classes("welcome-subtitle")
            ui.button("Return to dashboard", on_click=lambda: ui.navigate.to("/dashboard")).props(
                "outline no-caps"
            )
