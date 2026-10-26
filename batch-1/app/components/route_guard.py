"""Reusable authentication guard and safe local return-path handling."""

from __future__ import annotations

from urllib.parse import urlencode, urlsplit

from nicegui import app, ui

from app.config import Settings
from app.models.user import User
from app.services.auth_service import AUTHENTICATED_KEY, get_current_user, logout_user
from app.services.authorization_service import refreshed_profile

ALLOWED_RETURN_ROUTES = (
    "/dashboard",
    "/notifications",
    "/profile",
    "/settings",
    "/reports",
    "/tasks",
    "/templates",
    "/admin/",
)


def validate_return_path(value: str | None) -> str | None:
    if not value or "\\" in value or "\r" in value or "\n" in value:
        return None
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return None
    if not parsed.path.startswith("/") or parsed.path.startswith("//"):
        return None
    if not any(
        parsed.path.startswith(route)
        if route.endswith("/")
        else parsed.path == route or parsed.path.startswith(f"{route}/")
        for route in ALLOWED_RETURN_ROUTES
    ):
        return None
    return parsed.path + (f"?{parsed.query}" if parsed.query else "")


async def require_authentication(return_path: str) -> User | None:
    had_session = app.storage.user.get(AUTHENTICATED_KEY) is True
    user = get_current_user()
    if user is not None and not Settings.from_environment().demo_auth_enabled:
        user = await refreshed_profile(user)
    if user is not None:
        return user

    if had_session:
        logout_user()
    safe_return_path = validate_return_path(return_path) or "/dashboard"
    query: dict[str, str] = {"next": safe_return_path}
    if had_session:
        query["status"] = "session_expired"
    ui.navigate.to(f"/?{urlencode(query)}")
    return None
