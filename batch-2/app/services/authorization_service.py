"""Authoritative authorization checks for persisted application identities."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.auth_service import ROLE_LABELS, ROLE_PRECEDENCE, permissions_for_role_keys


@dataclass(frozen=True, slots=True)
class AuthorizedActor:
    user: ApplicationUser
    role_keys: tuple[str, ...]
    permissions: frozenset[str]


async def active_actor_for_profile(
    session: AsyncSession,
    profile: User,
) -> AuthorizedActor | None:
    """Re-resolve a session profile against active database state."""

    actor = await session.scalar(
        select(ApplicationUser).where(
            ApplicationUser.email == profile.email.casefold(),
            ApplicationUser.active.is_(True),
        )
    )
    if actor is None:
        return None
    role_keys = tuple(
        await session.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == actor.id, Role.active.is_(True))
            .order_by(Role.name)
        )
    )
    if not role_keys:
        return None
    permissions = (
        profile.permissions
        if Settings.from_environment().demo_auth_enabled
        else permissions_for_role_keys(role_keys)
    )
    return AuthorizedActor(actor, role_keys, permissions)


async def refreshed_profile(
    profile: User,
    manager: DatabaseManager = database,
) -> User | None:
    """Return current connected-user identity and roles, or ``None`` if revoked."""

    async with manager.session() as session:
        authorized = await active_actor_for_profile(session, profile)
        if authorized is None:
            return None
        primary_key = next(
            (key for key in ROLE_PRECEDENCE if key in authorized.role_keys),
            None,
        )
        if primary_key is None:
            return None
        return User(
            id=str(authorized.user.id),
            name=authorized.user.display_name,
            email=authorized.user.email,
            role=ROLE_LABELS[primary_key],
            permissions=authorized.permissions,
        )


async def require_permission(
    session: AsyncSession,
    profile: User,
    permission: str,
) -> AuthorizedActor:
    authorized = await active_actor_for_profile(session, profile)
    if authorized is None or permission not in authorized.permissions:
        raise PermissionError(f"{permission} is required for this operation.")
    return authorized


async def require_role_by_id(
    session: AsyncSession,
    actor_id: UUID,
    role_key: str,
) -> ApplicationUser:
    actor = await session.scalar(
        select(ApplicationUser)
        .join(UserRole, UserRole.user_id == ApplicationUser.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            ApplicationUser.id == actor_id,
            ApplicationUser.active.is_(True),
            Role.key == role_key,
            Role.active.is_(True),
        )
    )
    if actor is None:
        raise PermissionError(f"An active {role_key} role is required for this operation.")
    return actor
