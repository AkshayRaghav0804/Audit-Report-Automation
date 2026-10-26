"""Pure route decisions, kept separate for focused testing."""

from __future__ import annotations

from app.models.authentication import AuthenticationStatus


def sign_in_redirect(authenticated: bool) -> str | None:
    return "/dashboard" if authenticated else None


def dashboard_redirect(authenticated: bool) -> str | None:
    return None if authenticated else "/sign-in"


def callback_redirect(status: AuthenticationStatus) -> str:
    if status is AuthenticationStatus.AUTHORISED:
        return "/dashboard"
    status_routes = {
        AuthenticationStatus.ACCESS_NOT_ASSIGNED: "access_not_assigned",
        AuthenticationStatus.INACTIVE: "inactive",
        AuthenticationStatus.UNAUTHORISED_ORGANISATION: "unauthorised",
        AuthenticationStatus.FAILED: "authentication_failed",
    }
    return f"/sign-in?status={status_routes[status]}"
