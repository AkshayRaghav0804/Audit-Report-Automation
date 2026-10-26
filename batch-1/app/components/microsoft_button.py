"""Microsoft sign-in button with duplicate-request protection."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from nicegui import ui


class SignInGuard:
    def __init__(self) -> None:
        self.busy = False

    async def run(self, operation: Callable[[], Awaitable[bool]]) -> bool:
        if self.busy:
            return False
        self.busy = True
        keep_disabled = False
        try:
            keep_disabled = await operation()
            return True
        finally:
            if not keep_disabled:
                self.busy = False


def microsoft_sign_in_button(
    on_start: Callable[[], Awaitable[bool]],
) -> SignInGuard:
    guard = SignInGuard()

    async def handle_click() -> None:
        if guard.busy:
            return
        button.disable()
        icon.set_visibility(False)
        spinner.set_visibility(True)
        label.set_text("Connecting to Microsoft…")
        completed = await guard.run(on_start)
        if not completed or not guard.busy:
            spinner.set_visibility(False)
            icon.set_visibility(True)
            label.set_text("Sign in with Microsoft")
            button.enable()

    with (
        ui.button(on_click=handle_click)
        .props('no-caps unelevated aria-label="Sign in with Microsoft"')
        .classes("microsoft-button") as button
    ):
        button.mark("microsoft-sign-in")
        button.style("background: var(--brand-primary) !important")
        icon = (
            ui.image("/static/icons/microsoft.svg")
            .props('alt="" aria-hidden="true"')
            .classes("microsoft-icon")
        )
        spinner = ui.spinner(size="22px", color="white")
        spinner.set_visibility(False)
        label = ui.label("Sign in with Microsoft").classes("microsoft-button__label")
    return guard
