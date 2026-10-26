"""All-notifications page with local filters."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, cast

from nicegui import app, ui

from app.components.header import add_application_head, render_header
from app.components.notification_menu import NotificationState
from app.components.route_guard import require_authentication
from app.components.sidebar import render_sidebar
from app.models.notification import Notification
from app.services.navigation_service import notification_target

FILTERS = ("All", "Unread", "Approvals", "Rejections", "Comments", "Mentions")


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def register_notification_routes() -> None:
    @ui.page("/notifications", title="Notifications | QC Report Automation")
    async def notifications_page() -> None:
        user = await require_authentication("/notifications")
        if user is None:
            return

        add_application_head()
        state = await NotificationState.for_user(user.email, _storage())
        drawer = render_sidebar(user, "/notifications")
        render_header(user, "Notifications", state, drawer)
        selected_filter = "All"

        with ui.element("main").classes("app-main"):
            with ui.column().classes("page-title-block gap-1"):
                ui.label("Notifications").classes("welcome-title")
                ui.label("Review workflow activity, comments and mentions.").classes(
                    "welcome-subtitle"
                )

            with ui.row().classes("notification-filters items-center"):
                filter_buttons: dict[str, ui.button] = {}

                def select_filter(filter_name: str) -> None:
                    nonlocal selected_filter
                    selected_filter = filter_name
                    for name, button in filter_buttons.items():
                        button.classes(
                            add="notification-filter--active" if name == filter_name else None,
                            remove=None if name == filter_name else "notification-filter--active",
                        )
                    notification_list.refresh()

                for filter_name in FILTERS:
                    button = (
                        ui.button(
                            filter_name,
                            on_click=lambda name=filter_name: select_filter(name),
                        )
                        .props("flat no-caps")
                        .classes("notification-filter")
                    )
                    filter_buttons[filter_name] = button
                filter_buttons["All"].classes(add="notification-filter--active")

            @ui.refreshable
            def notification_list() -> None:
                filtered = tuple(
                    item
                    for item in state.notifications
                    if _matches_filter(item, state, selected_filter)
                )
                with ui.column().classes("all-notifications gap-0"):
                    if not filtered:
                        with ui.column().classes("empty-state items-center"):
                            ui.icon("notifications_none")
                            ui.label("No matching notifications").classes("empty-state__title")
                            ui.label("Try another filter.").classes("empty-state__message")
                    for notification in filtered:
                        _notification_item(notification, state)

            notification_list()


def _matches_filter(
    notification: Notification,
    state: NotificationState,
    selected_filter: str,
) -> bool:
    if selected_filter == "All":
        return True
    if selected_filter == "Unread":
        return not state.is_read(notification)
    if selected_filter == "Approvals":
        return notification.is_approval
    if selected_filter == "Rejections":
        return notification.notification_type in {"report_rejected", "report_returned"}
    if selected_filter == "Comments":
        return notification.notification_type in {"comment_added", "comment_reply"}
    if selected_filter == "Mentions":
        return notification.notification_type == "mention"
    return False


def _notification_item(notification: Notification, state: NotificationState) -> None:
    async def selected() -> None:
        state.mark_read(notification.notification_id)
        await state.persist_read(notification.notification_id)
        ui.navigate.to(notification_target(notification))

    read_class = " is-read" if state.is_read(notification) else ""
    with (
        ui.button(on_click=selected)
        .props("flat no-caps")
        .classes(f"all-notification-item{read_class}")
    ):
        ui.icon("notifications").classes("all-notification-item__icon")
        with ui.column().classes("gap-1 all-notification-item__copy"):
            with ui.row().classes("items-center gap-2"):
                ui.label(notification.title).classes("all-notification-item__title")
                if not state.is_read(notification):
                    ui.label("Unread").classes("status-badge status-badge--unread")
            ui.label(notification.message).classes("all-notification-item__message")
            ui.label(notification.created_at).classes("all-notification-item__time")
        ui.icon("chevron_right").classes("all-notification-item__arrow")
