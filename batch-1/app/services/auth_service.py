"""Application session boundary for connected Entra and local demo identities."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any, cast

from nicegui import app

from app.config import Settings
from app.data.mock_data import MOCK_CURRENT_USER
from app.models.user import User
from app.services.demo_role_service import permissions_for_demo_role
from app.services.session_service import (
    AUTHENTICATED_KEY as SESSION_AUTHENTICATED_KEY,
)
from app.services.session_service import (
    DISPLAY_NAME_KEY,
    EMAIL_KEY,
    ROLES_KEY,
    USER_ID_KEY,
    clear_session,
)

AUTHENTICATED_KEY = SESSION_AUTHENTICATED_KEY
NAME_KEY = "name"
ROLE_KEY = "role"
ACTING_IDENTITY_KEY = "acting_identity"

ROLE_LABELS: dict[str, str] = {
    "employee": "Employee",
    "manager": "Manager",
    "director": "Director",
    "partner": "Partner",
    "qc": "QC Reviewer",
    "eqcr": "EQCR Reviewer",
    "administrator": "Administrator",
}
ROLE_PRECEDENCE = (
    "administrator",
    "partner",
    "eqcr",
    "qc",
    "director",
    "manager",
    "employee",
)


def permissions_for_role_keys(role_keys: tuple[str, ...]) -> frozenset[str]:
    """Resolve application permissions from normalized persisted role keys."""

    normalized = tuple(dict.fromkeys(role.casefold() for role in role_keys))
    role_labels = tuple(ROLE_LABELS[role] for role in normalized if role in ROLE_LABELS)
    permissions = frozenset(
        permission
        for role_label in role_labels
        for permission in permissions_for_demo_role(role_label)
    )
    # Connected reviewer identities cannot inherit maker access from an additional
    # Employee/Manager membership. This mirrors the established sign-in contract.
    primary_key = next((role for role in ROLE_PRECEDENCE if role in normalized), None)
    if primary_key in {"director", "partner", "qc", "eqcr"}:
        permissions = permissions - {"report.create"}
    return permissions


def _user_storage() -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], app.storage.user)


def is_authenticated(storage: MutableMapping[str, Any] | None = None) -> bool:
    return get_current_user(storage) is not None


def get_current_user(storage: MutableMapping[str, Any] | None = None) -> User | None:
    current_storage = storage if storage is not None else _user_storage()
    if not Settings.from_environment().demo_auth_enabled:
        return _get_connected_user(current_storage)
    if current_storage.get(AUTHENTICATED_KEY) is not True:
        return None

    role = current_storage.get(ROLE_KEY)
    if not isinstance(role, str):
        return None
    try:
        permissions = permissions_for_demo_role(role)
    except ValueError:
        return None
    expected = MOCK_CURRENT_USER
    identity_values = {
        USER_ID_KEY: expected.id,
        NAME_KEY: expected.name,
        EMAIL_KEY: expected.email,
    }
    if any(current_storage.get(key) != value for key, value in identity_values.items()):
        acting_identity = current_storage.get(ACTING_IDENTITY_KEY)
        expected_acting_identity = {
            "id": current_storage.get(USER_ID_KEY),
            "name": current_storage.get(NAME_KEY),
            "email": current_storage.get(EMAIL_KEY),
            "role": role,
        }
        if acting_identity != expected_acting_identity:
            return None
    return User(
        id=str(current_storage[USER_ID_KEY]),
        name=str(current_storage[NAME_KEY]),
        email=str(current_storage[EMAIL_KEY]),
        role=role,
        permissions=permissions,
    )


def login_mock_user(storage: MutableMapping[str, Any] | None = None) -> User:
    if not Settings.from_environment().demo_auth_enabled:
        raise PermissionError(
            "Demo authentication is disabled. Configure Microsoft sign-in before granting access."
        )
    current_storage = storage if storage is not None else _user_storage()
    current_storage.clear()
    user = MOCK_CURRENT_USER
    current_storage.update(
        {
            AUTHENTICATED_KEY: True,
            USER_ID_KEY: user.id,
            NAME_KEY: user.name,
            EMAIL_KEY: user.email,
            ROLE_KEY: user.role,
        }
    )
    return user


def logout_user(storage: MutableMapping[str, Any] | None = None) -> None:
    current_storage = storage if storage is not None else _user_storage()
    clear_session(current_storage)


def set_demo_role(
    role: str,
    storage: MutableMapping[str, Any] | None = None,
    *,
    identity: User | None = None,
) -> User:
    """Change the temporary role and, when supplied, its workflow assignee identity."""

    if not Settings.from_environment().demo_auth_enabled:
        raise PermissionError("Demo role switching is disabled.")
    current_storage = storage if storage is not None else _user_storage()
    if get_current_user(current_storage) is None:
        raise PermissionError("An authenticated session is required to change roles.")
    permissions = permissions_for_demo_role(role)
    expected = MOCK_CURRENT_USER
    if identity is not None and identity.role != role:
        raise ValueError("The demo identity does not match the selected role.")
    selected = identity or User(
        id=expected.id,
        name=expected.name,
        email=expected.email,
        role=role,
        permissions=permissions,
    )
    current_storage.update(
        {
            USER_ID_KEY: selected.id,
            NAME_KEY: selected.name,
            EMAIL_KEY: selected.email,
            ROLE_KEY: role,
        }
    )
    if identity is None:
        current_storage.pop(ACTING_IDENTITY_KEY, None)
    else:
        current_storage[ACTING_IDENTITY_KEY] = {
            "id": selected.id,
            "name": selected.name,
            "email": selected.email,
            "role": role,
        }
    return User(
        id=selected.id,
        name=selected.name,
        email=selected.email,
        role=role,
        permissions=permissions,
    )


def _get_connected_user(storage: MutableMapping[str, Any]) -> User | None:
    if storage.get(AUTHENTICATED_KEY) is not True:
        return None
    user_id = storage.get(USER_ID_KEY)
    display_name = storage.get(DISPLAY_NAME_KEY)
    email = storage.get(EMAIL_KEY)
    stored_roles = storage.get(ROLES_KEY)
    if (
        not isinstance(user_id, str)
        or not user_id
        or not isinstance(display_name, str)
        or not display_name
        or not isinstance(email, str)
        or not email
        or not isinstance(stored_roles, list)
        or any(not isinstance(role, str) for role in stored_roles)
    ):
        return None

    role_keys = tuple(dict.fromkeys(role.casefold() for role in stored_roles))
    primary_key = next((role for role in ROLE_PRECEDENCE if role in role_keys), None)
    if primary_key is None:
        return None
    return User(
        id=user_id,
        name=display_name,
        email=email.casefold(),
        role=ROLE_LABELS[primary_key],
        permissions=permissions_for_role_keys(role_keys),
    )
