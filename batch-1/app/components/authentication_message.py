"""Controlled, accessible authentication feedback."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from nicegui import ui


class SignInStatus(StrEnum):
    SESSION_EXPIRED = "session_expired"
    ACCESS_NOT_ASSIGNED = "access_not_assigned"
    INACTIVE = "inactive"
    UNAUTHORISED = "unauthorised"
    AUTHENTICATION_FAILED = "authentication_failed"


@dataclass(frozen=True, slots=True)
class StatusContent:
    title: str
    message: str
    icon: str


STATUS_CONTENT: dict[SignInStatus, StatusContent] = {
    SignInStatus.SESSION_EXPIRED: StatusContent(
        "Session expired",
        "Your session has expired for security reasons. Sign in again to continue.",
        "schedule",
    ),
    SignInStatus.ACCESS_NOT_ASSIGNED: StatusContent(
        "Access not assigned",
        "Your Microsoft account was verified, but application access has not yet been "
        "assigned. Please contact the application administrator.",
        "person_off",
    ),
    SignInStatus.INACTIVE: StatusContent(
        "Account inactive",
        "Your application access is currently inactive. Please contact the application "
        "administrator.",
        "pause_circle",
    ),
    SignInStatus.UNAUTHORISED: StatusContent(
        "Account not authorised",
        "This Microsoft account is not authorised to access the application.",
        "block",
    ),
    SignInStatus.AUTHENTICATION_FAILED: StatusContent(
        "Sign-in unsuccessful",
        "We could not sign you in. Please try again or contact the application administrator.",
        "error_outline",
    ),
}


def parse_status(value: str | None) -> SignInStatus | None:
    if value is None:
        return None
    try:
        return SignInStatus(value)
    except ValueError:
        return None


def render_authentication_message(
    container: ui.column,
    status: SignInStatus,
) -> None:
    content = STATUS_CONTENT[status]
    container.clear()
    with container:
        with ui.row().classes("auth-message__heading items-center no-wrap"):
            ui.icon(content.icon).props('aria-hidden="true"')
            ui.label(content.title).classes("auth-message__title")
        ui.label(content.message).classes("auth-message__body")
        with ui.row().classes("auth-message__actions"):
            ui.link("Try again", "/").classes("text-link")
            ui.link("Contact administrator", "/support/contact").classes("text-link")


def clear_authentication_message(container: ui.column) -> None:
    container.clear()


def render_inline_error(container: ui.column, title: str, message: str) -> None:
    container.clear()
    with container:
        with ui.row().classes("auth-message__heading items-center no-wrap"):
            ui.icon("error_outline").props('aria-hidden="true"')
            ui.label(title).classes("auth-message__title")
        ui.label(message).classes("auth-message__body")
