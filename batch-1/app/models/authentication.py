"""Authentication result and identity models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AuthenticationStatus(StrEnum):
    AUTHORISED = "authorised"
    ACCESS_NOT_ASSIGNED = "access_not_assigned"
    INACTIVE = "inactive"
    UNAUTHORISED_ORGANISATION = "unauthorised_organisation"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MicrosoftIdentity:
    subject: str
    display_name: str
    email: str
    tenant_id: str


@dataclass(frozen=True, slots=True)
class ApplicationUser:
    display_name: str
    email: str
    active: bool
    roles: tuple[str, ...]
    id: str | None = None


@dataclass(frozen=True, slots=True)
class AuthenticationResult:
    status: AuthenticationStatus
    message: str | None = None
    user: ApplicationUser | None = None


@dataclass(frozen=True, slots=True)
class SignInRequest:
    authorization_url: str
    state: str
