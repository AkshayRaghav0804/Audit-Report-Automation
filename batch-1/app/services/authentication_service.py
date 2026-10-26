"""Microsoft Entra ID authentication integration."""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
from typing import Any, Protocol, cast

import msal

from app.config import Settings
from app.models.authentication import (
    AuthenticationResult,
    AuthenticationStatus,
    MicrosoftIdentity,
    SignInRequest,
)
from app.services.user_directory import ApplicationUserDirectory

LOGGER = logging.getLogger(__name__)
# MSAL adds the reserved OpenID scopes itself and rejects callers that repeat them.
SCOPES = ("email",)


class AuthenticationServiceError(RuntimeError):
    """Safe base error for authentication initiation failures."""


class AuthenticationConfigurationError(AuthenticationServiceError):
    """Raised when required Microsoft settings have not been supplied."""


class AuthenticationService(Protocol):
    async def start_microsoft_sign_in(self) -> SignInRequest: ...

    async def process_callback(
        self, code: str, returned_state: str, expected_state: str
    ) -> AuthenticationResult: ...


class MicrosoftEntraAuthenticationService:
    def __init__(
        self,
        settings: Settings,
        user_directory: ApplicationUserDirectory,
    ) -> None:
        self._settings = settings
        self._user_directory = user_directory

    async def start_microsoft_sign_in(self) -> SignInRequest:
        self._ensure_configured()
        state = secrets.token_urlsafe(32)
        authorization_url = self._create_client().get_authorization_request_url(
            scopes=list(SCOPES),
            state=state,
            redirect_uri=cast(str, self._settings.microsoft_redirect_uri),
            prompt="select_account",
        )
        return SignInRequest(authorization_url=authorization_url, state=state)

    async def process_callback(
        self, code: str, returned_state: str, expected_state: str
    ) -> AuthenticationResult:
        self._ensure_configured()
        if not expected_state or not hmac.compare_digest(returned_state, expected_state):
            LOGGER.warning("Rejected Microsoft callback with invalid state")
            return AuthenticationResult(AuthenticationStatus.FAILED)

        try:
            token_result = await asyncio.to_thread(self._exchange_code, code)
            identity = self._identity_from_token_result(token_result)
            if identity is None:
                return AuthenticationResult(AuthenticationStatus.FAILED)
            if not self._is_allowed_organisation(identity):
                return AuthenticationResult(AuthenticationStatus.UNAUTHORISED_ORGANISATION)

            application_user = await self._user_directory.find_by_microsoft_identity(identity)
            if application_user is None or not application_user.roles:
                return AuthenticationResult(AuthenticationStatus.ACCESS_NOT_ASSIGNED)
            if not application_user.active:
                return AuthenticationResult(AuthenticationStatus.INACTIVE)
            return AuthenticationResult(
                AuthenticationStatus.AUTHORISED,
                user=application_user,
            )
        except Exception:  # Microsoft/library details must never reach the browser.
            LOGGER.exception("Microsoft authentication callback failed")
            return AuthenticationResult(AuthenticationStatus.FAILED)

    def _ensure_configured(self) -> None:
        missing = self._settings.missing_microsoft_settings
        if missing:
            raise AuthenticationConfigurationError(
                "Microsoft Entra ID authentication is not configured. Missing: "
                + ", ".join(missing)
                + "."
            )

    def _create_client(self) -> msal.ConfidentialClientApplication:
        authority = f"https://login.microsoftonline.com/{self._settings.microsoft_tenant_id}"
        return msal.ConfidentialClientApplication(
            client_id=cast(str, self._settings.microsoft_client_id),
            client_credential=cast(str, self._settings.microsoft_client_secret),
            authority=authority,
        )

    def _exchange_code(self, code: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._create_client().acquire_token_by_authorization_code(
                code=code,
                scopes=list(SCOPES),
                redirect_uri=cast(str, self._settings.microsoft_redirect_uri),
            ),
        )

    @staticmethod
    def _identity_from_token_result(
        token_result: dict[str, Any],
    ) -> MicrosoftIdentity | None:
        # MSAL validates the ID token before exposing these identity claims.
        raw_claims = token_result.get("id_token_claims")
        if not isinstance(raw_claims, dict):
            LOGGER.warning(
                "Microsoft token exchange failed with code %s",
                token_result.get("error", "missing_id_token"),
            )
            return None
        claims = cast(dict[str, object], raw_claims)
        email = claims.get("preferred_username") or claims.get("email")
        # This is a single-tenant workforce application. Bind the immutable Entra
        # object ID, never a fallback subject or an email supplied by the browser.
        subject = claims.get("oid")
        tenant_id = claims.get("tid")
        if (
            not isinstance(email, str)
            or not email
            or not isinstance(subject, str)
            or not subject
            or not isinstance(tenant_id, str)
            or not tenant_id
        ):
            LOGGER.warning("Microsoft identity did not include required claims")
            return None
        display_name = claims.get("name")
        return MicrosoftIdentity(
            subject=subject,
            display_name=display_name if isinstance(display_name, str) else email,
            email=email.lower(),
            tenant_id=tenant_id,
        )

    def _is_allowed_organisation(self, identity: MicrosoftIdentity) -> bool:
        configured_tenant = cast(str, self._settings.microsoft_tenant_id).lower()
        configured_domain = cast(str, self._settings.allowed_microsoft_domain).lower().lstrip("@")
        email_domain = identity.email.rsplit("@", maxsplit=1)[-1]
        return identity.tenant_id.lower() == configured_tenant and email_domain == configured_domain
