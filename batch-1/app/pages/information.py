"""Safe placeholders for support and legal links."""

from __future__ import annotations

from nicegui import ui

from app.theme import configure_theme


def register_information_routes() -> None:
    pages = {
        "/support/contact": (
            "Contact administrator",
            "Contact your application administrator or internal IT service desk to request access.",
        ),
        "/legal/privacy": (
            "Privacy notice",
            "The organisation's approved privacy notice will be published here.",
        ),
        "/legal/terms": (
            "Terms of use",
            "The organisation's approved internal terms of use will be published here.",
        ),
    }

    for route, (title, message) in pages.items():
        _register_information_page(route, title, message)


def _register_information_page(route: str, title: str, message: str) -> None:
    @ui.page(route, title=f"{title} | Audit Report Review Tool")
    async def information_page() -> None:
        configure_theme()
        with ui.column().classes("w-full min-h-screen items-center justify-center p-8"):
            ui.label(title).classes("text-3xl font-semibold")
            ui.label(message).classes("max-w-xl text-center text-slate-600")
            ui.link("Return to sign-in", "/").classes("text-primary")
