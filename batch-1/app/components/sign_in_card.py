"""Sign-in card composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nicegui import ui

from app.components.authentication_message import SignInStatus, render_authentication_message
from app.components.microsoft_button import microsoft_sign_in_button


def sign_in_card(
    *,
    initial_status: SignInStatus | None,
    on_sign_in: Callable[[ui.column], Awaitable[bool]],
) -> None:
    with ui.element("main").classes("sign-in-card").props('aria-labelledby="welcome-heading"'):
        ui.html('<h2 id="welcome-heading" class="sign-in-title">Welcome</h2>', sanitize=False)
        ui.label("Sign in using your organisational Microsoft account.").classes(
            "sign-in-supporting"
        )
        status_region = ui.column().classes("auth-message")
        status_region.props('role="status" aria-live="polite" aria-atomic="true"')
        if initial_status is not None:
            render_authentication_message(status_region, initial_status)

        async def start() -> bool:
            return await on_sign_in(status_region)

        microsoft_sign_in_button(start)
        ui.label("Use your authorised company Microsoft account to continue.").classes(
            "development-note"
        )
        with ui.row().classes("permission-note items-start no-wrap"):
            ui.icon("admin_panel_settings").props('aria-hidden="true"')
            ui.label(
                "Access is available only to users authorised by the application administrator."
            )
        ui.separator().classes("card-divider")
        with ui.column().classes("support-area"):
            ui.label("Need access?").classes("support-area__label")
            with ui.row().classes("support-links"):
                ui.link("Contact administrator", "/support/contact").classes("text-link")
                ui.link("Privacy notice", "/legal/privacy").classes("text-link")
                ui.link("Terms of use", "/legal/terms").classes("text-link")
