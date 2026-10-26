"""Transactional people-directory operations and role-aware search."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models.actor import (
    ApplicationUser,
    ProfessionalCredential,
    Role,
    UserRole,
)
from app.database.session import DatabaseManager, database
from app.models.report_template import PartnerReference
from app.models.user import User
from app.repositories.directory_repository import DirectoryRepository
from app.schemas.directory import DirectoryPerson, PersonUpsert
from app.services.authorization_service import require_permission

ROLE_CATALOG: dict[str, str] = {
    "employee": "Employee",
    "manager": "Manager",
    "director": "Director",
    "partner": "Partner",
    "qc": "QC",
    "eqcr": "EQCR",
    "administrator": "Administrator",
}


class DirectoryConflictError(ValueError):
    """Raised when stable identity or credential keys conflict."""


class DirectoryService:
    def __init__(self, manager: DatabaseManager = database) -> None:
        self.manager = manager

    async def list_people(
        self,
        *,
        role_key: str | None = None,
        search: str | None = None,
        active_only: bool = True,
        limit: int | None = 500,
    ) -> tuple[DirectoryPerson, ...]:
        async with self.manager.session() as session:
            return await DirectoryRepository(session).list_people(
                role_key=role_key,
                search=search,
                active_only=active_only,
                limit=limit,
            )

    async def find_person_by_email(self, email: str) -> DirectoryPerson | None:
        async with self.manager.session() as session:
            return await DirectoryRepository(session).find_person_by_email(email)

    async def save_people(
        self,
        people: Iterable[PersonUpsert],
        *,
        profile: User | None = None,
    ) -> None:
        async with self.manager.session() as session:
            async with session.begin():
                if profile is not None or not Settings.from_environment().demo_auth_enabled:
                    if profile is None:
                        raise PermissionError(
                            "An authenticated administrator is required to change the directory."
                        )
                    await require_permission(session, profile, "user.manage")
                await ensure_role_catalog(session)
                for person in people:
                    await upsert_person(session, person)

    async def ensure_source_partners(self, partners: Iterable[PartnerReference]) -> None:
        """Expose controlled Word-source signatories in the application directory.

        The stable ICAI-based employee code lets an administrator later replace the
        placeholder email with the partner's real directory identity without creating
        a duplicate person. Existing ICAI owners are preserved and only gain the
        Partner role when it is missing.
        """

        async with self.manager.session() as session:
            async with session.begin():
                roles = await ensure_role_catalog(session)
                partner_role = roles["partner"]
                repository = DirectoryRepository(session)
                for partner in partners:
                    membership_number = partner.icai_membership_number
                    employee_code = f"ICAI-{membership_number}"
                    credential = await repository.find_credential("ICAI", membership_number)
                    if credential is None:
                        await upsert_person(
                            session,
                            PersonUpsert(
                                employee_code=employee_code,
                                display_name=partner.name,
                                email=f"icai-{membership_number}@partner.invalid",
                                department="Audit",
                                roles=("partner",),
                                icai_membership_number=membership_number,
                                active=True,
                            ),
                        )
                        continue

                    owner = await repository.find_user_by_id(credential.user_id)
                    if owner is None:
                        continue
                    if owner.employee_code == employee_code and owner.email.endswith(
                        "@partner.invalid"
                    ):
                        owner.display_name = partner.name
                        owner.active = True
                        credential.active = True
                    has_partner_role = await session.scalar(
                        select(UserRole.id).where(
                            UserRole.user_id == owner.id,
                            UserRole.role_id == partner_role.id,
                        )
                    )
                    if has_partner_role is None:
                        session.add(UserRole(user_id=owner.id, role_id=partner_role.id))

    async def ensure_profile_person(self, profile: User) -> DirectoryPerson:
        """Provision the temporary local-demo profile in the application directory."""

        existing = await self.find_person_by_email(profile.email)
        if not Settings.from_environment().demo_auth_enabled:
            if existing is None or not existing.active:
                raise PermissionError("Your active people-directory account could not be found.")
            return existing
        role_key = role_key_for_profile(profile.role)
        roles = tuple(dict.fromkeys((*existing.roles, role_key))) if existing else (role_key,)
        employee_code = (
            existing.employee_code
            if existing
            else "PROFILE-" + hashlib.sha256(profile.email.encode()).hexdigest()[:10].upper()
        )
        await self.save_people(
            (
                PersonUpsert(
                    employee_code=employee_code,
                    display_name=profile.name,
                    email=profile.email,
                    department=existing.department if existing else None,
                    roles=roles,
                    icai_membership_number=(existing.icai_membership_number if existing else None),
                    active=True,
                ),
            )
        )
        person = await self.find_person_by_email(profile.email)
        if person is None:
            raise RuntimeError("Unable to provision the current application profile.")
        return person


def role_key_for_profile(role: str) -> str:
    return {
        "QC Reviewer": "qc",
        "EQCR Reviewer": "eqcr",
        "Administrator": "administrator",
    }.get(role, role.casefold().replace(" ", "_"))


async def ensure_role_catalog(session: AsyncSession) -> dict[str, Role]:
    roles = {
        role.key: role
        for role in await session.scalars(select(Role).where(Role.key.in_(ROLE_CATALOG)))
    }
    for key, name in ROLE_CATALOG.items():
        if key not in roles:
            role = Role(key=key, name=name, active=True)
            session.add(role)
            roles[key] = role
    await session.flush()
    return roles


async def upsert_person(session: AsyncSession, person: PersonUpsert) -> ApplicationUser:
    repository = DirectoryRepository(session)
    by_employee_code = await repository.find_user_by_employee_code(person.employee_code)
    by_email = await repository.find_user_by_email(person.email)
    if by_employee_code is not None and by_email is not None and by_employee_code.id != by_email.id:
        raise DirectoryConflictError(
            f"Employee code {person.employee_code} and email {person.email} "
            "belong to different directory users."
        )
    user = by_employee_code or by_email
    if user is None:
        user = ApplicationUser(
            employee_code=person.employee_code,
            display_name=person.display_name,
            email=person.email,
            department=person.department,
            active=person.active,
        )
        session.add(user)
        await session.flush()
    else:
        user.employee_code = person.employee_code
        user.display_name = person.display_name
        user.email = person.email
        user.department = person.department
        user.active = person.active

    roles = await ensure_role_catalog(session)
    unknown_roles = set(person.roles).difference(roles)
    if unknown_roles:
        raise DirectoryConflictError("Unknown roles: " + ", ".join(sorted(unknown_roles)))
    existing_role_ids = set(
        await session.scalars(select(UserRole.role_id).where(UserRole.user_id == user.id))
    )
    desired_role_ids = {roles[role_key].id for role_key in person.roles}
    if removed_role_ids := existing_role_ids - desired_role_ids:
        await session.execute(
            delete(UserRole).where(
                UserRole.user_id == user.id,
                UserRole.role_id.in_(removed_role_ids),
            )
        )
    for role_id in desired_role_ids - existing_role_ids:
        session.add(UserRole(user_id=user.id, role_id=role_id))

    current_credentials = tuple(
        await session.scalars(
            select(ProfessionalCredential).where(
                ProfessionalCredential.user_id == user.id,
                ProfessionalCredential.professional_body == "ICAI",
                ProfessionalCredential.active.is_(True),
            )
        )
    )
    requested_number = person.icai_membership_number
    if requested_number:
        credential_owner = await repository.find_credential("ICAI", requested_number)
        if credential_owner is not None and credential_owner.user_id != user.id:
            raise DirectoryConflictError(
                f"ICAI membership number {requested_number} is already assigned."
            )
    for credential in current_credentials:
        if credential.membership_number != requested_number:
            credential.active = False
    if requested_number and not any(
        credential.membership_number == requested_number for credential in current_credentials
    ):
        session.add(
            ProfessionalCredential(
                user_id=user.id,
                professional_body="ICAI",
                membership_number=requested_number,
                active=True,
            )
        )
    await session.flush()
    return user


async def directory_person_for_user(session: AsyncSession, user_id: UUID) -> DirectoryPerson | None:
    user = await session.get(ApplicationUser, user_id)
    if user is None:
        return None
    people = await DirectoryRepository(session).list_people(
        search=user.email, active_only=False, limit=10
    )
    return next((person for person in people if person.id == user_id), None)
