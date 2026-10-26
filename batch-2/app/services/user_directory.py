"""Application user lookup boundary."""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError

from app.config import Settings
from app.database.models.actor import ApplicationUser as DatabaseUser
from app.database.models.actor import Role, UserRole
from app.database.session import DatabaseManager, database
from app.models.authentication import ApplicationUser, MicrosoftIdentity

LOGGER = logging.getLogger(__name__)


class ApplicationUserDirectory(Protocol):
    async def find_by_microsoft_identity(
        self, identity: MicrosoftIdentity
    ) -> ApplicationUser | None: ...


class UnconfiguredUserDirectory:
    """Safe default: verified Microsoft identities have no application access."""

    async def find_by_microsoft_identity(
        self, identity: MicrosoftIdentity
    ) -> ApplicationUser | None:
        del identity
        return None


class DatabaseApplicationUserDirectory:
    """Resolve verified Entra identities, optionally registering new employees.

    Call only after Microsoft token verification and organisation validation. The
    registration path also checks the configured tenant and exact email domain.
    """

    def __init__(
        self, manager: DatabaseManager = database, *, settings: Settings | None = None
    ) -> None:
        self._manager = manager
        self._settings = settings if settings is not None else Settings.from_environment()

    async def find_by_microsoft_identity(
        self, identity: MicrosoftIdentity
    ) -> ApplicationUser | None:
        try:
            return await self._resolve_identity(identity, create=self._may_register(identity))
        except IntegrityError:
            # Another first sign-in (or an administrator) may have inserted or
            # bound this identity concurrently. The failed transaction is fully
            # rolled back; re-read once and apply all normal conflict checks.
            # Never blindly update an account or retry registration on conflict.
            return await self._resolve_identity(identity, create=False)

    def _may_register(self, identity: MicrosoftIdentity) -> bool:
        tenant = self._settings.microsoft_tenant_id
        domain = self._settings.allowed_microsoft_domain
        local_part, separator, email_domain = identity.email.partition("@")
        return bool(
            self._settings.auto_provision_employees
            and not self._settings.demo_auth_enabled
            and tenant
            and domain
            and identity.tenant_id.casefold() == tenant.casefold()
            and separator
            and local_part
            and email_domain.casefold() == domain.casefold().lstrip("@")
            and not any(character.isspace() for character in identity.email)
            and len(identity.email) <= 320
            and 0 < len(identity.subject) <= 100
        )

    async def _resolve_identity(
        self, identity: MicrosoftIdentity, *, create: bool
    ) -> ApplicationUser | None:
        email = identity.email.casefold()
        async with self._manager.session() as session:
            async with session.begin():
                users = tuple(
                    await session.scalars(
                        select(DatabaseUser)
                        .where(
                            or_(
                                DatabaseUser.entra_object_id == identity.subject,
                                DatabaseUser.email == email,
                            )
                        )
                        .order_by(DatabaseUser.id)
                        .with_for_update()
                    )
                )
                by_object_id = next(
                    (user for user in users if user.entra_object_id == identity.subject), None
                )
                by_email = next((user for user in users if user.email == email), None)
                if (
                    by_object_id is not None
                    and by_email is not None
                    and by_object_id.id != by_email.id
                ):
                    LOGGER.warning("Entra object ID and email matched different application users")
                    return None

                user = by_object_id or by_email
                if user is None:
                    if not create:
                        return None
                    employee_role = await session.scalar(
                        select(Role).where(Role.key == "employee", Role.active.is_(True))
                    )
                    if employee_role is None:
                        LOGGER.warning("Employee registration requires an active Employee role")
                        return None
                    identity_key = f"{identity.tenant_id.casefold()}\0{identity.subject}"
                    employee_code = (
                        "ENTRA-"
                        + hashlib.sha256(identity_key.encode("utf-8")).hexdigest()[:40].upper()
                    )
                    user = DatabaseUser(
                        employee_code=employee_code,
                        display_name=(identity.display_name.strip() or email)[:200],
                        email=email,
                        entra_object_id=identity.subject,
                        active=True,
                    )
                    session.add(user)
                    await session.flush()
                    session.add(UserRole(user_id=user.id, role_id=employee_role.id))
                    await session.flush()
                if user.entra_object_id not in {None, identity.subject}:
                    LOGGER.warning("Rejected Entra identity with a conflicting stored object ID")
                    return None
                roles = tuple(
                    await session.scalars(
                        select(Role.key)
                        .join(UserRole, UserRole.role_id == Role.id)
                        .where(
                            UserRole.user_id == user.id,
                            Role.active.is_(True),
                        )
                        .order_by(Role.name)
                    )
                )
                # Do not alter inactive or deliberately unassigned accounts, and
                # never give existing accounts a new default role at sign-in.
                if user.entra_object_id is None and user.active and roles:
                    user.entra_object_id = identity.subject
                return ApplicationUser(
                    display_name=user.display_name,
                    email=user.email,
                    active=user.active,
                    roles=roles,
                    id=str(user.id),
                )
