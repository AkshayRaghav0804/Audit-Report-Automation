"""Fixed application header."""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urlencode

from nicegui import ui

from app.components.notification_menu import NotificationState, notification_menu
from app.config import Settings
from app.models.user import User
from app.services.auth_service import logout_user, set_demo_role
from app.services.demo_role_identity_service import DemoRoleIdentityService
from app.services.demo_role_service import DEMO_ROLES
from app.theme import PRIMARY_COLOR, configure_theme, dashboard_theme_html


def add_application_head() -> None:
    configure_theme()
    ui.add_head_html(
        dashboard_theme_html() + '<link rel="stylesheet" href="/static/styles/dashboard.css?v=34">'
        f'<meta name="theme-color" content="{PRIMARY_COLOR}">'
    )


def render_header(
    user: User,
    current_page: str,
    notifications: NotificationState,
    drawer: ui.left_drawer,
) -> None:
    with ui.header(elevated=False).classes("app-header items-center no-wrap"):
        with ui.row().classes("header-left items-center no-wrap"):
            with (
                ui.button(icon="menu", on_click=drawer.toggle)
                .props('flat round aria-label="Toggle navigation"')
                .classes("mobile-menu-button")
            ):
                ui.tooltip("Toggle navigation")
            with ui.column().classes("page-identity gap-0"):
                ui.label("QC Report Automation").classes("page-identity__app")
                ui.label(current_page).classes("page-identity__page")

        ui.element("div").classes("header-spacer")

        with ui.row().classes("header-actions items-center no-wrap"):
            search = (
                ui.input(placeholder="Search reports and clients")
                .props('outlined dense clearable aria-label="Search reports and clients"')
                .classes("header-search")
            )
            search.add_slot("prepend", '<q-icon name="search" />')
            search.on(
                "keydown.enter",
                lambda: _search_reports(search.value),
            )
            notification_menu(notifications)

            profile_menu: ui.menu
            with (
                ui.button(on_click=lambda: profile_menu.open())
                .props('flat no-caps aria-label="Open profile menu"')
                .classes("profile-button")
                .mark("profile-menu-button")
            ):
                ui.label(user.initials).classes("profile-avatar")
                with ui.column().classes("profile-copy gap-0"):
                    ui.label(user.name).classes("profile-name")
                    ui.label(user.role).classes("profile-role").mark("profile-current-role")
                ui.icon("expand_more").classes("profile-chevron")
                with (
                    ui.menu()
                    .props("anchor='bottom right' self='top right'")
                    .classes("profile-menu")
                ) as profile_menu:
                    _profile_action("person", "My Profile", lambda: ui.navigate.to("/profile"))
                    _profile_action("settings", "Settings", lambda: ui.navigate.to("/settings"))
                    if Settings.from_environment().demo_auth_enabled:
                        ui.separator()
                        ui.label("WORKSPACE ROLE").classes("profile-menu__section-label")
                        for role in DEMO_ROLES:
                            _role_action(role, user.role)
                    ui.separator()
                    _profile_action("logout", "Sign Out", _sign_out)


def _sign_out() -> None:
    logout_user()
    ui.navigate.to("/")


def _search_reports(value: object) -> None:
    query = str(value or "").strip()
    if not query:
        ui.notify(
            "Enter a report name, client, or engagement reference.",
            type="warning",
            position="top-right",
        )
        return
    ui.navigate.to(f"/reports?{urlencode({'search': query})}")


def _profile_action(
    icon: str,
    label: str,
    on_select: Callable[[], None] | None = None,
) -> None:
    def selected() -> None:
        if on_select is not None:
            on_select()
            return
        ui.notify(f"{label} is not configured for this workspace.", position="top-right")

    button = ui.button(on_click=selected).props("flat no-caps").classes("profile-menu__item")
    if label == "Sign Out":
        button.mark("sign-out-action")
    with button:
        ui.icon(icon)
        ui.label(label)


def _role_action(role: str, current_role: str) -> None:
    async def selected() -> None:
        try:
            identity = await DemoRoleIdentityService().identity_for(role)
            set_demo_role(role, identity=identity)
        except (OSError, PermissionError, RuntimeError, ValueError) as error:
            ui.notify(str(error), type="negative", position="top-right")
            return
        ui.navigate.to("/dashboard")

    active_class = " profile-role-option--active" if role == current_role else ""
    button = (
        ui.button(on_click=selected)
        .props("flat no-caps")
        .classes(f"profile-menu__item profile-role-option{active_class}")
        .mark("demo-role-" + role.casefold().replace(" ", "-"))
    )
    with button:
        ui.icon("check_circle" if role == current_role else "badge")
        ui.label(role)
