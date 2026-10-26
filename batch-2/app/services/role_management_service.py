"""Authorized database role membership management."""

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.session import DatabaseManager, database
from app.models.user import User
from app.services.demo_role_service import permissions_for_demo_role
from app.services.report_edit_role_service import ROLE_LABELS


class RoleManagementService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def _authorize(self, session: AsyncSession, profile: User) -> ApplicationUser:
        actor = await session.scalar(
            select(ApplicationUser).where(
                ApplicationUser.email == profile.email.casefold(), ApplicationUser.active.is_(True)
            )
        )
        if actor is None or not await session.scalar(
            select(UserRole.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id == actor.id, Role.key == "administrator", Role.active.is_(True))
        ):
            raise PermissionError("Only active directory administrators can manage roles.")
        return actor

    async def catalog(self, profile: User) -> list[dict[str, str]]:
        async with self.manager.session() as session:
            await self._authorize(session, profile)
            roles = await session.scalars(select(Role).order_by(Role.name))
            return [
                {
                    "key": role.key,
                    "name": role.name,
                    "status": "Active" if role.active else "Inactive",
                    "permissions": ", ".join(
                        sorted(permissions_for_demo_role(ROLE_LABELS[role.key]))
                    )
                    if role.key in ROLE_LABELS
                    else "No application permissions",
                }
                for role in roles
            ]

    async def assign(self, profile: User, person_id: UUID, role_keys: list[str]) -> None:
        async with self.manager.session() as session:
            async with session.begin():
                actor = await self._authorize(session, profile)
                if person_id == actor.id:
                    raise ValueError("Use another administrator to change your own roles.")
                person = await session.get(ApplicationUser, person_id)
                if person is None or not person.active:
                    raise ValueError("Select an active directory person.")
                roles = list(
                    await session.scalars(
                        select(Role).where(Role.active.is_(True), Role.key.in_(role_keys))
                    )
                )
                if not role_keys or {role.key for role in roles} != set(role_keys):
                    raise ValueError("Select at least one active role.")
                await session.execute(delete(UserRole).where(UserRole.user_id == person_id))
                session.add_all(UserRole(user_id=person_id, role_id=role.id) for role in roles)
