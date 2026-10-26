"""Minimal application-session operations."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

from app.models.authentication import ApplicationUser

AUTHENTICATED_KEY = "authenticated"
USER_ID_KEY = "user_id"
DISPLAY_NAME_KEY = "display_name"
EMAIL_KEY = "email"
ROLES_KEY = "roles"
OAUTH_STATE_KEY = "microsoft_oauth_state"
SIGN_IN_PENDING_KEY = "sign_in_pending"
RETURN_PATH_KEY = "sign_in_return_path"


def is_authenticated(storage: MutableMapping[str, Any]) -> bool:
    return storage.get(AUTHENTICATED_KEY) is True


def establish_session(storage: MutableMapping[str, Any], user: ApplicationUser) -> None:
    if not user.id:
        raise ValueError("An application user ID is required to establish a connected session.")
    clear_session(storage)
    storage.update(
        {
            AUTHENTICATED_KEY: True,
            USER_ID_KEY: user.id,
            DISPLAY_NAME_KEY: user.display_name,
            EMAIL_KEY: user.email,
            ROLES_KEY: list(user.roles),
        }
    )


def clear_session(storage: MutableMapping[str, Any]) -> None:
    storage.clear()


def pop_oauth_state(storage: MutableMapping[str, Any]) -> str:
    value = storage.pop(OAUTH_STATE_KEY, "")
    storage.pop(SIGN_IN_PENDING_KEY, None)
    return value if isinstance(value, str) else ""


def pop_return_path(storage: MutableMapping[str, Any]) -> str:
    value = storage.pop(RETURN_PATH_KEY, "")
    return value if isinstance(value, str) else ""
