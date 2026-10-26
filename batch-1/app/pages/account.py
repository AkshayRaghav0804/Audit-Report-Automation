"""Signed-in profile and browser-session display preferences."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, cast

from nicegui import app, ui
from sqlalchemy.exc import SQLAlchemyError

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.services.auth_service import ROLE_LABELS
from app.services.directory_service import DirectoryService
from app.services.display_preferences_service import (
    PAGE_SIZES,
    report_page_size,
    save_display_preferences,
)
from app.services.permission_service import has_permission

LOGGER = logging.getLogger(__name__)


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_account_routes() -> None:
    @ui.page("/profile", title="My Profile | QC Report Automation", response_timeout=10)
    async def profile_page() -> None:
        user = await require_authentication("/profile")
        if user is None:
            return
        add_application_head()
        notifications = await NotificationState.for_user(user.email, _storage())
        drawer = render_sidebar(user, "/profile")
        render_header(user, "My Profile", notifications, drawer)
        with ui.element("main").classes("app-main account-page"):
            ui.label("My Profile").classes("welcome-title")
            workspace = (
                "Report preparation"
                if has_permission(user, "report.create")
                else "Quality control review"
                if user.role == "QC Reviewer"
                else "Engagement quality review"
                if user.role == "EQCR Reviewer"
                else "Report review and approvals"
            )
            ui.label(f"{workspace} · Your work account and application access.").classes(
                "welcome-subtitle"
            )
            ui.link(
                "Open reports" if has_permission(user, "report.create") else "My review tasks",
                "/reports" if has_permission(user, "report.create") else "/tasks",
            )
            try:
                person = await DirectoryService().find_person_by_email(user.email)
            except (SQLAlchemyError, RuntimeError):
                LOGGER.exception("Unable to load signed-in profile")
                ui.label("Your profile is temporarily unavailable. Please try again.").classes(
                    "text-negative"
                )
                ui.button("Retry", on_click=lambda: ui.navigate.to("/profile"))
                return
            with ui.card().classes("account-card"):
                _profile_field("Name", person.display_name if person else user.name)
                _profile_field("Work email", user.email)
                _profile_field("Current role", user.role)
                if person:
                    _profile_field("Employee code", person.employee_code)
                    _profile_field("Department", person.department or "Not provided")
                    _profile_field(
                        "Assigned roles",
                        ", ".join(ROLE_LABELS.get(role, role) for role in person.roles) or "None",
                    )
                    _profile_field("Account status", "Active" if person.active else "Inactive")
                    if person.icai_membership_number:
                        _profile_field("ICAI membership number", person.icai_membership_number)
                else:
                    ui.label("Your account is not listed in the people directory.").classes(
                        "text-negative"
                    )
            ui.label(
                "Contact your application administrator to correct employee details "
                "or change assigned roles."
            ).classes("welcome-subtitle")
            ui.link("Display settings", "/settings").classes("section-link")

    @ui.page("/settings", title="Settings | QC Report Automation", response_timeout=10)
    async def settings_page() -> None:
        user = await require_authentication("/settings")
        if user is None:
            return
        add_application_head()
        notifications = await NotificationState.for_user(user.email, _storage())
        drawer = render_sidebar(user, "/settings")
        render_header(user, "Settings", notifications, drawer)
        with ui.element("main").classes("app-main account-page"):
            ui.label("Settings").classes("welcome-title")
            ui.label(
                "Display preferences for this browser session. "
                "Signing out resets these preferences."
            ).classes("welcome-subtitle")
            with ui.card().classes("account-card"):
                ui.label("Report lists and navigation").classes("section-title")
                page_size = (
                    ui.select(
                        list(PAGE_SIZES),
                        value=report_page_size(_storage()),
                        label="Reports per page",
                    )
                    .props("outlined")
                    .classes("w-full")
                )
                collapsed = ui.switch(
                    "Use a compact sidebar", value=_storage().get("sidebar_collapsed") is True
                )

                def save() -> None:
                    save_display_preferences(
                        _storage(), page_size=int(page_size.value), collapsed=bool(collapsed.value)
                    )
                    ui.navigate.to("/settings?saved=true")

                ui.button("Save settings", icon="save", on_click=save).props("unelevated no-caps")
                if (
                    ui.context.client.request
                    and ui.context.client.request.query_params.get("saved") == "true"
                ):
                    ui.label("Settings saved").props('role="status"').classes("text-positive")
            with ui.card().classes("account-card"):
                ui.label("Report notifications").classes("section-title")
                ui.label(
                    "Approval requests, returned reports, comments and final approvals "
                    "follow your report assignments."
                )
                ui.label(f"Your notification address: {user.email}").classes("account-field-value")
                ui.link("Open notifications", "/notifications").classes("section-link")


def _profile_field(label: str, value: str) -> None:
    with ui.column().classes("gap-1 account-field"):
        ui.label(label).classes("section-subtitle")
        ui.label(value).classes("account-field-value")
