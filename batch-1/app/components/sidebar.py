"""Permission-aware application sidebar."""

from __future__ import annotations

from nicegui import app, ui

from app.models.user import User
from app.services.navigation_service import (
    ADMIN_NAVIGATION,
    PRIMARY_NAVIGATION,
    NavigationItem,
    visible_items,
)

SIDEBAR_COLLAPSED_KEY = "sidebar_collapsed"


def render_sidebar(user: User, current_path: str) -> ui.left_drawer:
    collapsed = app.storage.user.get(SIDEBAR_COLLAPSED_KEY) is True
    drawer = (
        ui.left_drawer(value=None)
        .props("width=250 mini-width=72 breakpoint=900 bordered")
        .classes("app-sidebar")
    )
    if collapsed:
        drawer.props(add="mini")

    with drawer:
        with ui.column().classes("sidebar-content gap-0"):
            with ui.row().classes("sidebar-brand items-center no-wrap"):
                ui.label("KKC").classes("sidebar-brand__mark")

            with ui.element("nav").classes("sidebar-navigation"):
                ui.label("WORKSPACE").classes("sidebar-section-label nav-label")
                _navigation_group(user, PRIMARY_NAVIGATION, current_path)

                admin_items = visible_items(user, ADMIN_NAVIGATION)
                if admin_items:
                    ui.label("ADMINISTRATION").classes("sidebar-section-label nav-label")
                    _navigation_group(user, ADMIN_NAVIGATION, current_path)

            def toggle_mini() -> None:
                nonlocal collapsed
                collapsed = not collapsed
                app.storage.user[SIDEBAR_COLLAPSED_KEY] = collapsed
                if collapsed:
                    drawer.props(add="mini")
                    collapse_icon.set_name("keyboard_double_arrow_right")
                    collapse_button.props('aria-label="Expand sidebar"')
                    collapse_tooltip.set_text("Expand sidebar")
                else:
                    drawer.props(remove="mini")
                    collapse_icon.set_name("keyboard_double_arrow_left")
                    collapse_button.props('aria-label="Collapse sidebar"')
                    collapse_tooltip.set_text("Collapse sidebar")

            collapse_label = "Expand sidebar" if collapsed else "Collapse sidebar"
            collapse_button = (
                ui.button(on_click=toggle_mini)
                .props(f'flat no-caps aria-label="{collapse_label}"')
                .classes("sidebar-collapse")
                .mark("sidebar-collapse-toggle")
            )
            with collapse_button:
                collapse_icon = ui.icon(
                    "keyboard_double_arrow_right" if collapsed else "keyboard_double_arrow_left"
                )
                ui.label("Collapse sidebar").classes("nav-label")
                collapse_tooltip = ui.tooltip(collapse_label)
    return drawer


def _navigation_group(
    user: User,
    items: tuple[NavigationItem, ...],
    current_path: str,
) -> None:
    for item in visible_items(user, items):
        active = current_path == item.route or (
            item.route != "/dashboard" and current_path.startswith(item.route)
        )
        active_class = " nav-item--active" if active else ""
        with (
            ui.link(target=item.route)
            .classes(f"nav-item{active_class}")
            .props(f'aria-label="{item.label}"')
        ):
            ui.icon(item.icon).classes("nav-item__icon")
            ui.label(item.label).classes("nav-label")
            ui.tooltip(item.label)
