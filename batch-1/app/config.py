"""Environment-backed application configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from dotenv import dotenv_values

DEFAULT_DATABASE_URL = "postgresql+asyncpg://qc_app@localhost:5432/qc_automation"
LOCAL_POSTGRES_ENV_PATH = Path(__file__).resolve().parent.parent / ".env.postgres.local"
DEFAULT_DATABASE_POOL_SIZE = 5
DEFAULT_DATABASE_MAX_OVERFLOW = 10
DEFAULT_DATABASE_POOL_TIMEOUT_SECONDS = 30
DEFAULT_DATABASE_POOL_RECYCLE_SECONDS = 1_800
DEFAULT_DATABASE_STATEMENT_TIMEOUT_MS = 30_000
MINIMUM_STORAGE_SECRET_LENGTH = 32


@dataclass(frozen=True, slots=True)
class Settings:
    microsoft_client_id: str | None
    microsoft_client_secret: str | None
    microsoft_tenant_id: str | None
    microsoft_redirect_uri: str | None
    allowed_microsoft_domain: str | None
    nicegui_storage_secret: str | None
    demo_auth_enabled: bool = False
    application_base_url: str = "http://localhost:8080"
    microsoft_graph_sender_email: str | None = None
    qc_team_email: str | None = None
    database_url: str = DEFAULT_DATABASE_URL
    database_echo: bool = False
    database_pool_size: int = DEFAULT_DATABASE_POOL_SIZE
    database_max_overflow: int = DEFAULT_DATABASE_MAX_OVERFLOW
    database_pool_timeout_seconds: int = DEFAULT_DATABASE_POOL_TIMEOUT_SECONDS
    database_pool_recycle_seconds: int = DEFAULT_DATABASE_POOL_RECYCLE_SECONDS
    database_statement_timeout_ms: int = DEFAULT_DATABASE_STATEMENT_TIMEOUT_MS
    auto_provision_employees: bool = False
    entra_employee_group_id: str | None = None

    @classmethod
    def from_environment(cls) -> Settings:
        return cls(
            microsoft_client_id=_read_optional("MICROSOFT_CLIENT_ID"),
            microsoft_client_secret=_read_optional("MICROSOFT_CLIENT_SECRET"),
            microsoft_tenant_id=_read_optional("MICROSOFT_TENANT_ID"),
            microsoft_redirect_uri=_read_optional("MICROSOFT_REDIRECT_URI"),
            allowed_microsoft_domain=_read_optional("ALLOWED_MICROSOFT_DOMAIN"),
            nicegui_storage_secret=_read_optional("NICEGUI_STORAGE_SECRET"),
            demo_auth_enabled=_read_bool("QC_ENABLE_DEMO_AUTH", default=False),
            auto_provision_employees=_read_bool("QC_AUTO_PROVISION_EMPLOYEES", default=False),
            entra_employee_group_id=_read_optional("QC_ENTRA_EMPLOYEE_GROUP_ID"),
            application_base_url=_application_base_url(),
            microsoft_graph_sender_email=_read_optional("MICROSOFT_GRAPH_SENDER_EMAIL"),
            qc_team_email=_read_optional("QC_TEAM_EMAIL"),
            database_url=(
                _read_optional("QC_DATABASE_URL")
                or _read_local_postgres_database_url()
                or DEFAULT_DATABASE_URL
            ),
            database_echo=_read_bool("QC_DATABASE_ECHO", default=False),
            database_pool_size=_read_positive_int(
                "QC_DATABASE_POOL_SIZE", default=DEFAULT_DATABASE_POOL_SIZE
            ),
            database_max_overflow=_read_nonnegative_int(
                "QC_DATABASE_MAX_OVERFLOW", default=DEFAULT_DATABASE_MAX_OVERFLOW
            ),
            database_pool_timeout_seconds=_read_positive_int(
                "QC_DATABASE_POOL_TIMEOUT_SECONDS",
                default=DEFAULT_DATABASE_POOL_TIMEOUT_SECONDS,
            ),
            database_pool_recycle_seconds=_read_positive_int(
                "QC_DATABASE_POOL_RECYCLE_SECONDS",
                default=DEFAULT_DATABASE_POOL_RECYCLE_SECONDS,
            ),
            database_statement_timeout_ms=_read_positive_int(
                "QC_DATABASE_STATEMENT_TIMEOUT_MS",
                default=DEFAULT_DATABASE_STATEMENT_TIMEOUT_MS,
            ),
        )

    @property
    def missing_microsoft_settings(self) -> tuple[str, ...]:
        configured = {
            "MICROSOFT_CLIENT_ID": self.microsoft_client_id,
            "MICROSOFT_CLIENT_SECRET": self.microsoft_client_secret,
            "MICROSOFT_TENANT_ID": self.microsoft_tenant_id,
            "MICROSOFT_REDIRECT_URI": self.microsoft_redirect_uri,
            "ALLOWED_MICROSOFT_DOMAIN": self.allowed_microsoft_domain,
        }
        return tuple(name for name, value in configured.items() if not value)

    def require_storage_secret(self) -> str:
        if not self.nicegui_storage_secret:
            raise RuntimeError(
                "NICEGUI_STORAGE_SECRET is required. Load the variables from .env.example "
                "and provide a long random secret."
            )
        if len(self.nicegui_storage_secret) < MINIMUM_STORAGE_SECRET_LENGTH:
            raise RuntimeError(
                "NICEGUI_STORAGE_SECRET must contain at least "
                f"{MINIMUM_STORAGE_SECRET_LENGTH} characters. Generate a new random value; "
                "do not reuse a password."
            )
        return self.nicegui_storage_secret

    def require_safe_application_host(self, host: str) -> None:
        """Keep the intentionally weak local demo identity off shared interfaces."""

        if self.demo_auth_enabled and not _is_loopback_host(host):
            raise RuntimeError(
                "QC_ENABLE_DEMO_AUTH may only be used when QC_APP_HOST is a loopback host "
                "such as 127.0.0.1 or ::1. Disable demo authentication before exposing "
                "the application to a network."
            )

    @property
    def graph_mail_configured(self) -> bool:
        return not self.demo_auth_enabled and all(
            (
                self.microsoft_client_id,
                self.microsoft_client_secret,
                self.microsoft_tenant_id,
            )
        )


def _application_base_url() -> str:
    configured = _read_optional("QC_APPLICATION_BASE_URL")
    if configured:
        return configured.rstrip("/")
    redirect = urlsplit(_read_optional("MICROSOFT_REDIRECT_URI") or "")
    if redirect.scheme in {"http", "https"} and redirect.netloc:
        path = redirect.path.removesuffix("/auth/callback").rstrip("/")
        return urlunsplit((redirect.scheme, redirect.netloc, path, "", ""))
    return "http://localhost:8080"


def _read_optional(name: str) -> str | None:
    value = os.getenv(name, "").strip()
    return value or None


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().removeprefix("[").removesuffix("]")
    if normalized.casefold() == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _read_local_postgres_database_url() -> str | None:
    if not LOCAL_POSTGRES_ENV_PATH.is_file():
        return None
    value = dotenv_values(LOCAL_POSTGRES_ENV_PATH).get("QC_DATABASE_URL")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _read_bool(name: str, *, default: bool) -> bool:
    value = os.getenv(name, "").strip().lower()
    if not value:
        return default
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def _read_positive_int(name: str, *, default: int) -> int:
    return _read_int(name, default=default, minimum=1)


def _read_nonnegative_int(name: str, *, default: int) -> int:
    return _read_int(name, default=default, minimum=0)


def _read_int(name: str, *, default: int, minimum: int) -> int:
    value = os.getenv(name, "").strip()
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if parsed < minimum:
        qualifier = "positive" if minimum == 1 else "non-negative"
        raise ValueError(f"{name} must be {qualifier}.")
    return parsed
