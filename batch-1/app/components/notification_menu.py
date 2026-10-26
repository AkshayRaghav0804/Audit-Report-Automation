"""Session-local notification state and header notification menu."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from nicegui import ui

from app.data.mock_data import MOCK_NOTIFICATIONS
from app.models.notification import Notification
from app.services.navigation_service import notification_target
from app.services.notification_service import NotificationService

READ_NOTIFICATION_IDS = "mock_read_notification_ids"


class NotificationState:
    def __init__(
        self,
        storage: MutableMapping[str, Any],
        *,
        notifications: tuple[Notification, ...] = MOCK_NOTIFICATIONS,
        user_email: str | None = None,
        unread_count: int | None = None,
    ) -> None:
        self._storage = storage
        self._notifications = notifications
        self._user_email = user_email
        initial_read = {item.notification_id for item in notifications if item.is_read}
        stored = storage.get(READ_NOTIFICATION_IDS, [])
        if isinstance(stored, list):
            initial_read.update(item for item in stored if isinstance(item, str))
        self._read_ids = initial_read
        if unread_count is None:
            self._unread_count = sum(
                item.notification_id not in self._read_ids for item in notifications
            )
        else:
            locally_read = sum(
                item.notification_id in self._read_ids and not item.is_read
                for item in notifications
            )
            self._unread_count = max(0, unread_count - locally_read)
        self._persist()

    @classmethod
    async def for_user(
        cls,
        email: str,
        storage: MutableMapping[str, Any],
    ) -> NotificationState:
        inbox = await NotificationService().inbox_for_user(email)
        return cls(
            storage,
            notifications=inbox.notifications,
            user_email=email,
            unread_count=inbox.unread_count,
        )

    @property
    def notifications(self) -> tuple[Notification, ...]:
        return self._notifications

    @property
    def unread_count(self) -> int:
        return self._unread_count

    def is_read(self, notification: Notification) -> bool:
        return notification.notification_id in self._read_ids

    def mark_read(self, notification_id: str) -> None:
        was_unread = notification_id not in self._read_ids and any(
            item.notification_id == notification_id and not item.is_read
            for item in self._notifications
        )
        self._read_ids.add(notification_id)
        if was_unread:
            self._unread_count = max(0, self._unread_count - 1)
        self._persist()

    def mark_all_read(self) -> None:
        self._read_ids.update(item.notification_id for item in self._notifications)
        self._unread_count = 0
        self._persist()

    async def persist_read(self, notification_id: str) -> None:
        if self._user_email:
            await NotificationService().mark_read(self._user_email, notification_id)

    async def persist_all_read(self) -> None:
        if self._user_email:
            await NotificationService().mark_all_read(self._user_email)

    def _persist(self) -> None:
        self._storage[READ_NOTIFICATION_IDS] = sorted(self._read_ids)


def notification_menu(state: NotificationState) -> None:
    with (
        ui.button(icon="notifications_none")
        .props('flat round aria-label="Open notifications"')
        .classes("header-icon-button")
    ):
        unread_badge = (
            ui.badge(str(state.unread_count)).props("floating").classes("notification-count")
        )
        unread_badge.set_visibility(state.unread_count > 0)
        ui.tooltip("Notifications")
        with ui.menu().props("anchor='bottom right' self='top right'").classes("notification-menu"):
            with ui.row().classes("notification-menu__heading items-center justify-between"):
                with ui.column().classes("gap-0"):
                    ui.label("Notifications").classes("notification-menu__title")
                    ui.label(f"{state.unread_count} unread").classes("notification-menu__subtitle")

                async def mark_all() -> None:
                    state.mark_all_read()
                    await state.persist_all_read()
                    unread_badge.set_visibility(False)
                    ui.notify("All notifications marked as read", position="top-right")

                ui.button("Mark all as read", on_click=mark_all).props(
                    "flat no-caps dense"
                ).classes("notification-menu__mark-all")
            ui.separator()
            with ui.column().classes("notification-menu__list gap-0"):
                for notification in state.notifications[:5]:
                    _notification_row(notification, state, unread_badge)
            ui.separator()
            ui.link("View all notifications", "/notifications").classes(
                "notification-menu__view-all"
            )


def _notification_row(
    notification: Notification,
    state: NotificationState,
    badge: ui.badge,
) -> None:
    async def open_notification() -> None:
        state.mark_read(notification.notification_id)
        await state.persist_read(notification.notification_id)
        badge.set_text(str(state.unread_count))
        badge.set_visibility(state.unread_count > 0)
        ui.navigate.to(notification_target(notification))

    read_class = " is-read" if state.is_read(notification) else ""
    with (
        ui.button(on_click=open_notification)
        .props("flat no-caps")
        .classes(f"notification-row{read_class}")
    ):
        ui.icon(_notification_icon(notification.notification_type)).classes(
            "notification-row__icon"
        )
        with ui.column().classes("notification-row__copy gap-0"):
            with ui.row().classes("items-center no-wrap gap-2"):
                ui.label(notification.title).classes("notification-row__title")
                if not state.is_read(notification):
                    ui.element("span").classes("notification-row__unread-dot").props(
                        'aria-label="Unread"'
                    )
            ui.label(notification.message).classes("notification-row__message")
            ui.label(notification.created_at).classes("notification-row__time")


def _notification_icon(notification_type: str) -> str:
    return {
        "comment_added": "comment",
        "mention": "alternate_email",
        "comment_reply": "reply",
        "report_submitted": "approval",
        "report_approved": "task_alt",
        "report_rejected": "cancel",
        "report_returned": "assignment_return",
        "reviewer_assigned": "person_add",
        "approval_stage_changed": "account_tree",
    }.get(notification_type, "notifications")
