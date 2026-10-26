"""Database queries for people, roles, and professional credentials."""

from __future__ import annotations

from collections import defaultdict
from typing import cast
from uuid import UUID

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.actor import ApplicationUser, ProfessionalCredential, Role, UserRole
from app.schemas.directory import DirectoryPerson


class DirectoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_people(
        self,
        *,
        role_key: str | None = None,
        search: str | None = None,
        active_only: bool = True,
        limit: int | None = 500,
    ) -> tuple[DirectoryPerson, ...]:
        statement: Select[tuple[ApplicationUser]] = select(ApplicationUser)
        if role_key:
            statement = (
                statement.join(UserRole, UserRole.user_id == ApplicationUser.id)
                .join(Role, Role.id == UserRole.role_id)
                .where(Role.key == role_key.casefold())
            )
        if active_only:
            statement = statement.where(ApplicationUser.active.is_(True))
        if search and search.strip():
            term = f"%{search.strip()}%"
            statement = statement.where(
                or_(
                    ApplicationUser.display_name.ilike(term),
                    ApplicationUser.email.ilike(term),
                    ApplicationUser.employee_code.ilike(term),
                )
            )
        statement = statement.order_by(ApplicationUser.display_name, ApplicationUser.email).limit(
            limit
        )
        users = tuple((await self.session.scalars(statement)).unique())
        return await self._to_people(users)

    async def find_user_by_email(self, email: str) -> ApplicationUser | None:
        return cast(
            ApplicationUser | None,
            await self.session.scalar(
                select(ApplicationUser).where(ApplicationUser.email == email.casefold())
            ),
        )

    async def find_person_by_email(self, email: str) -> DirectoryPerson | None:
        user = await self.find_user_by_email(email)
        if user is None:
            return None
        return (await self._to_people((user,)))[0]

    async def find_user_by_employee_code(self, employee_code: str) -> ApplicationUser | None:
        return cast(
            ApplicationUser | None,
            await self.session.scalar(
                select(ApplicationUser).where(
                    ApplicationUser.employee_code == employee_code.upper()
                )
            ),
        )

    async def find_user_by_id(self, user_id: UUID) -> ApplicationUser | None:
        return await self.session.get(ApplicationUser, user_id)

    async def find_role(self, role_key: str) -> Role | None:
        return cast(
            Role | None,
            await self.session.scalar(select(Role).where(Role.key == role_key.casefold())),
        )

    async def find_credential(
        self, professional_body: str, membership_number: str
    ) -> ProfessionalCredential | None:
        return cast(
            ProfessionalCredential | None,
            await self.session.scalar(
                select(ProfessionalCredential).where(
                    ProfessionalCredential.professional_body == professional_body.upper(),
                    ProfessionalCredential.membership_number == membership_number,
                )
            ),
        )

    async def _to_people(self, users: tuple[ApplicationUser, ...]) -> tuple[DirectoryPerson, ...]:
        if not users:
            return ()
        user_ids = tuple(user.id for user in users)
        role_rows = await self.session.execute(
            select(UserRole.user_id, Role.key)
            .join(Role, Role.id == UserRole.role_id)
            .where(UserRole.user_id.in_(user_ids))
            .order_by(Role.name)
        )
        roles: defaultdict[UUID, list[str]] = defaultdict(list)
        for user_id, role_key in role_rows:
            roles[user_id].append(role_key)

        credential_rows = await self.session.execute(
            select(
                ProfessionalCredential.user_id,
                ProfessionalCredential.membership_number,
            ).where(
                ProfessionalCredential.user_id.in_(user_ids),
                ProfessionalCredential.professional_body == "ICAI",
                ProfessionalCredential.active.is_(True),
            )
        )
        credentials = {user_id: membership_number for user_id, membership_number in credential_rows}
        return tuple(
            DirectoryPerson(
                id=user.id,
                employee_code=user.employee_code,
                display_name=user.display_name,
                email=user.email,
                department=user.department,
                roles=tuple(roles[user.id]),
                icai_membership_number=credentials.get(user.id),
                active=user.active,
            )
            for user in users
        )
