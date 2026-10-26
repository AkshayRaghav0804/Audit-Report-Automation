"""Microsoft Entra sign-in, callback, and local demo entry points."""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, cast
from urllib.parse import urlencode

from nicegui import app, ui

from app.components.authentication_message import SignInStatus, parse_status, render_inline_error
from app.components.brand_panel import brand_identity, brand_panel
from app.components.route_guard import validate_return_path
from app.components.sign_in_card import sign_in_card
from app.config import Settings
from app.models.authentication import AuthenticationStatus
from app.pages.navigation import callback_redirect
from app.services.auth_service import (
    AUTHENTICATED_KEY,
    get_current_user,
    login_mock_user,
    logout_user,
)
from app.services.authentication_service import (
    AuthenticationConfigurationError,
    MicrosoftEntraAuthenticationService,
)
from app.services.session_service import (
    OAUTH_STATE_KEY,
    RETURN_PATH_KEY,
    SIGN_IN_PENDING_KEY,
    clear_session,
    establish_session,
    pop_oauth_state,
    pop_return_path,
)
from app.services.user_directory import DatabaseApplicationUserDirectory
from app.theme import configure_theme

SIGN_IN_RESPONSE_TIMEOUT_SECONDS = 10.0
LOGGER = logging.getLogger(__name__)


def _storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def _microsoft_authentication_service() -> MicrosoftEntraAuthenticationService:
    settings = Settings.from_environment()
    return MicrosoftEntraAuthenticationService(
        settings,
        DatabaseApplicationUserDirectory(settings=settings),
    )


def _page_head() -> None:
    configure_theme()
    ui.add_head_html(
        '<link rel="stylesheet" href="/static/styles/sign_in.css?v=2">'
        '<meta name="theme-color" content="#173b35">'
    )


def register_sign_in_routes() -> None:
    @ui.page(
        "/",
        title="Sign in | QC Report Automation",
        response_timeout=SIGN_IN_RESPONSE_TIMEOUT_SECONDS,
    )
    async def sign_in_page(
        next: str | None = None,
        status: str | None = None,
    ) -> None:
        if get_current_user() is not None:
            ui.navigate.to("/dashboard")
            return

        initial_status = parse_status(status)
        if app.storage.user.get(AUTHENTICATED_KEY) is True:
            logout_user()
            initial_status = SignInStatus.SESSION_EXPIRED

        _page_head()
        destination = validate_return_path(next) or "/dashboard"
        demo_auth_enabled = Settings.from_environment().demo_auth_enabled

        async def begin_sign_in(status_region: ui.column) -> bool:
            if demo_auth_enabled:
                login_mock_user()
                ui.navigate.to(destination)
                return True
            try:
                request = await _microsoft_authentication_service().start_microsoft_sign_in()
            except AuthenticationConfigurationError as error:
                LOGGER.error("Microsoft Entra sign-in is not configured: %s", error)
                render_inline_error(
                    status_region,
                    "Sign-in not configured",
                    "Microsoft Entra ID authentication has not been fully configured. "
                    "Please contact the application administrator.",
                )
                return False
            except Exception:
                LOGGER.exception("Microsoft Entra sign-in initiation failed")
                render_inline_error(
                    status_region,
                    "Sign-in unavailable",
                    "Microsoft sign-in could not be started. Please try again or contact "
                    "the application administrator.",
                )
                return False

            storage = _storage()
            storage[OAUTH_STATE_KEY] = request.state
            storage[RETURN_PATH_KEY] = destination
            storage[SIGN_IN_PENDING_KEY] = True
            ui.navigate.to(request.authorization_url)
            return True

        with ui.element("div").classes("sign-in-shell"):
            brand_panel()
            with (
                ui.element("section")
                .classes("authentication-panel")
                .props('aria-label="Microsoft sign-in"')
            ):
                with ui.column().classes("mobile-brand"):
                    brand_identity(compact=True)
                sign_in_card(initial_status=initial_status, on_sign_in=begin_sign_in)

    @ui.page("/sign-in", title="Sign in | QC Report Automation")
    async def legacy_sign_in_redirect(
        next: str | None = None,
        status: str | None = None,
    ) -> None:
        query = {
            key: value for key, value in (("next", next), ("status", status)) if value is not None
        }
        ui.navigate.to("/" + (f"?{urlencode(query)}" if query else ""))

    async def process_microsoft_callback(
        code: str | None,
        state: str | None,
        error: str | None,
    ) -> None:
        storage = _storage()
        expected_state = pop_oauth_state(storage)
        destination = validate_return_path(pop_return_path(storage)) or "/dashboard"
        if error or not code or not state:
            clear_session(storage)
            ui.navigate.to(callback_redirect(AuthenticationStatus.FAILED))
            return

        result = await _microsoft_authentication_service().process_callback(
            code,
            state,
            expected_state,
        )
        if result.status is AuthenticationStatus.AUTHORISED and result.user is not None:
            establish_session(storage, result.user)
            ui.navigate.to(destination)
            return

        clear_session(storage)
        ui.navigate.to(callback_redirect(result.status))

    async def render_microsoft_callback(
        code: str | None,
        state: str | None,
        error: str | None,
    ) -> None:
        _page_head()
        with ui.column().classes("fixed-center items-center gap-4"):
            ui.spinner(size="48px", color="primary")
            ui.label("Signing you in…").classes("text-h5")
            ui.label("Completing secure Microsoft authentication.").classes("text-body1")
        await ui.context.client.connected()
        await process_microsoft_callback(code, state, error)

    @ui.page(
        "/auth/callback",
        title="Signing in | QC Report Automation",
        response_timeout=SIGN_IN_RESPONSE_TIMEOUT_SECONDS,
    )
    async def microsoft_callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> None:
        await render_microsoft_callback(code, state, error)

    @ui.page(
        "/auth/microsoft/callback",
        title="Signing in | QC Report Automation",
        response_timeout=SIGN_IN_RESPONSE_TIMEOUT_SECONDS,
    )
    async def legacy_microsoft_callback(
        code: str | None = None,
        state: str | None = None,
        error: str | None = None,
    ) -> None:
        await render_microsoft_callback(code, state, error)
