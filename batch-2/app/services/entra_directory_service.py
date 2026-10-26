"""Preview and import employees from Microsoft Entra without requiring first sign-in."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import aiohttp
import msal
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.database.models.actor import ApplicationUser, Role, UserRole
from app.database.models.enums import ImportKind, ImportStatus
from app.database.models.import_data import MasterDataImportBatch
from app.database.models.mixins import utc_now
from app.database.session import DatabaseManager, database
from app.schemas.directory import PersonUpsert

GRAPH_ROOT = "https://graph.microsoft.com/v1.0"
MAX_USERS = 10_000
MAX_PAGES = 200
PREVIEW_LIFETIME = timedelta(minutes=15)
SELECT_FIELDS = (
    "id,displayName,mail,userPrincipalName,department,employeeId,accountEnabled,userType"
)


class EntraImportError(ValueError):
    """An actionable import failure safe to display without tokens or raw responses."""


@dataclass(frozen=True, slots=True)
class EntraEmployee:
    object_id: str
    upn: str
    person: PersonUpsert


@dataclass(frozen=True, slots=True)
class EntraImportRow:
    employee: EntraEmployee
    action: str
    detail: str


@dataclass(frozen=True, slots=True)
class EntraImportPreview:
    tenant_id: str
    group_id: str | None
    created_at: datetime
    rows: tuple[EntraImportRow, ...]
    excluded_count: int

    def count(self, action: str) -> int:
        return sum(row.action == action for row in self.rows)

    @property
    def can_commit(self) -> bool:
        return not self.count("error") and bool(self.count("insert") + self.count("update"))


class EntraDirectoryService:
    def __init__(
        self, manager: DatabaseManager = database, settings: Settings | None = None
    ) -> None:
        self.manager = manager
        self.settings = settings or Settings.from_environment()

    @property
    def configured(self) -> bool:
        return bool(self.settings.graph_mail_configured and self.settings.allowed_microsoft_domain)

    def _scope(self) -> tuple[str, str | None]:
        if not self.configured:
            raise EntraImportError(
                "Configure the Microsoft tenant ID, client ID, client secret and allowed "
                "company domain, and disable demo sign-in before importing from Entra."
            )
        try:
            tenant = str(UUID(self.settings.microsoft_tenant_id or ""))
            group = (
                str(UUID(self.settings.entra_employee_group_id))
                if self.settings.entra_employee_group_id
                else None
            )
        except ValueError as error:
            raise EntraImportError(
                "Tenant ID and employee group Object ID must be valid UUIDs."
            ) from error
        return tenant, group

    async def preview(self, *, actor_id: UUID) -> EntraImportPreview:
        tenant, group = self._scope()
        async with self.manager.session() as session:
            await _require_administrator(session, actor_id)
        employees, excluded = await self._fetch_employees(tenant, group)
        async with self.manager.session() as session:
            rows = await _plan(session, employees)
        return EntraImportPreview(tenant, group, utc_now(), rows, excluded)

    async def commit(self, preview: EntraImportPreview, *, actor_id: UUID) -> dict[str, int]:
        tenant, group = self._scope()
        if (tenant, group) != (preview.tenant_id, preview.group_id):
            raise EntraImportError("Entra configuration changed. Fetch a new preview.")
        if utc_now() - preview.created_at > PREVIEW_LIFETIME:
            raise EntraImportError("This preview has expired. Fetch employees again.")
        if not preview.can_commit:
            raise EntraImportError("Resolve preview errors before importing employees.")
        employees = tuple(row.employee for row in preview.rows)
        try:
            async with self.manager.session() as session:
                async with session.begin():
                    await _require_administrator(session, actor_id)
                    # Re-read under locks: preserve role, credential and activation changes
                    # made since preview, and abort the whole import on identity conflicts.
                    rows = await _plan(session, employees, lock=True)
                    if any(row.action == "error" for row in rows):
                        raise EntraImportError(
                            "The directory changed. Fetch a new preview to resolve conflicts."
                        )
                    employee_role = await session.scalar(
                        select(Role).where(Role.key == "employee", Role.active.is_(True))
                    )
                    if employee_role is None:
                        raise EntraImportError(
                            "An active Employee role is required before importing."
                        )
                    counts = {
                        action: sum(row.action == action for row in rows)
                        for action in ("insert", "update", "unchanged", "skip")
                    }
                    for row in rows:
                        if row.action not in {"insert", "update"}:
                            continue
                        employee = row.employee
                        person = employee.person
                        user = await _find_user(session, employee)
                        if user is None:
                            user = ApplicationUser(
                                employee_code=person.employee_code,
                                display_name=person.display_name,
                                email=person.email,
                                department=person.department,
                                entra_object_id=employee.object_id,
                                active=True,
                            )
                            session.add(user)
                            await session.flush()
                            session.add(UserRole(user_id=user.id, role_id=employee_role.id))
                        else:
                            user.display_name = person.display_name
                            user.email = person.email
                            user.department = person.department
                            user.entra_object_id = employee.object_id
                        await session.flush()
                    digest = hashlib.sha256(
                        json.dumps(
                            [(e.object_id, e.person.model_dump()) for e in employees],
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    session.add(
                        MasterDataImportBatch(
                            kind=ImportKind.PEOPLE,
                            filename="Microsoft Entra employee import",
                            file_sha256=digest,
                            status=ImportStatus.COMMITTED,
                            uploaded_by_user_id=actor_id,
                            preview_json={
                                "source": "entra",
                                "tenant_id": tenant,
                                "group_id": group,
                                "counts": counts,
                                "excluded": preview.excluded_count,
                            },
                            inserted_count=counts["insert"],
                            updated_count=counts["update"],
                            unchanged_count=counts["unchanged"],
                            warning_count=counts["skip"],
                            error_count=0,
                            committed_at=utc_now(),
                        )
                    )
                    return counts
        except IntegrityError as error:
            raise EntraImportError(
                "A directory identity changed during import. No employees were imported; "
                "fetch a new preview."
            ) from error

    def _access_token(self, tenant: str) -> str:
        try:
            client = msal.ConfidentialClientApplication(
                self.settings.microsoft_client_id,
                authority=f"https://login.microsoftonline.com/{tenant}",
                client_credential=self.settings.microsoft_client_secret,
            )
            result = client.acquire_token_for_client(
                scopes=["https://graph.microsoft.com/.default"]
            )
        except Exception as error:
            raise EntraImportError(
                "Could not connect to Microsoft. Check the app credentials and connectivity."
            ) from error
        token = result.get("access_token")
        if not isinstance(token, str) or not token:
            raise EntraImportError(
                "Microsoft authentication failed. Check the app credentials and admin consent."
            )
        return token

    async def _fetch_employees(
        self, tenant: str, group: str | None
    ) -> tuple[tuple[EntraEmployee, ...], int]:
        token = await asyncio.to_thread(self._access_token, tenant)
        path = f"/groups/{group}/members/microsoft.graph.user" if group else "/users"
        url = f"{GRAPH_ROOT}{path}?{urlencode({'$select': SELECT_FIELDS, '$top': 999})}"
        visited: set[str] = set()
        employees: dict[str, EntraEmployee] = {}
        excluded = total = 0
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as client:
                while url:
                    _validate_graph_url(url, path)
                    if url in visited or len(visited) >= MAX_PAGES:
                        raise EntraImportError(
                            "Microsoft pagination did not complete. No import was prepared."
                        )
                    visited.add(url)
                    page = await self._get_page(client, url, token)
                    values = page.get("value")
                    if not isinstance(values, list) or any(not isinstance(v, dict) for v in values):
                        raise EntraImportError(
                            "Microsoft returned an invalid employee list. Try fetching again."
                        )
                    total += len(values)
                    if total > MAX_USERS:
                        raise EntraImportError(
                            "More than 10,000 accounts were returned. "
                            "Configure a smaller employee group."
                        )
                    for value in values:
                        employee = _parse_employee(
                            value, tenant, self.settings.allowed_microsoft_domain or ""
                        )
                        if employee is None:
                            excluded += 1
                        elif (
                            employee.object_id in employees
                            and employees[employee.object_id] != employee
                        ):
                            raise EntraImportError(
                                "An employee changed during Microsoft pagination. Fetch again."
                            )
                        else:
                            employees[employee.object_id] = employee
                    next_link = page.get("@odata.nextLink")
                    if next_link is not None and (not isinstance(next_link, str) or not next_link):
                        raise EntraImportError(
                            "Microsoft returned invalid pagination. Try fetching again."
                        )
                    url = next_link or ""
        except (aiohttp.ClientError, TimeoutError) as error:
            raise EntraImportError(
                "Could not finish reading Microsoft employees. Nothing was saved; try again."
            ) from error
        return tuple(employees.values()), excluded

    async def _get_page(
        self, client: aiohttp.ClientSession, url: str, token: str
    ) -> dict[str, Any]:
        async with client.get(
            url, headers={"Authorization": f"Bearer {token}"}, allow_redirects=False
        ) as response:
            if response.status == 403:
                raise EntraImportError(
                    "Microsoft denied directory access. Grant User.Read.All (Application) "
                    "and admin consent. A configured employee group also needs "
                    "GroupMember.ReadBasic.All (Application)."
                )
            if response.status == 429:
                raise EntraImportError(
                    "Microsoft is limiting requests. Wait a minute and fetch again."
                )
            if response.status != 200:
                raise EntraImportError(
                    f"Microsoft directory request failed (HTTP {response.status}). "
                    "Check permissions and the group Object ID."
                )
            data = bytearray()
            async for chunk in response.content.iter_chunked(65_536):
                data.extend(chunk)
                if len(data) > 5_000_000:
                    raise EntraImportError("Microsoft returned an oversized directory page.")
            try:
                page = json.loads(data)
            except (ValueError, UnicodeError) as error:
                raise EntraImportError(
                    "Microsoft returned an unreadable directory page."
                ) from error
            if not isinstance(page, dict):
                raise EntraImportError("Microsoft returned an invalid directory page.")
            return page


def _validate_graph_url(url: str, path: str) -> None:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "graph.microsoft.com"
        or parsed.path != f"/v1.0{path}"
        or parsed.fragment
    ):
        raise EntraImportError("Microsoft returned an unexpected pagination address.")


def _parse_employee(value: dict[str, Any], tenant: str, domain: str) -> EntraEmployee | None:
    if not isinstance(value.get("accountEnabled"), bool) or value.get("userType") not in {
        "Member",
        "Guest",
    }:
        raise EntraImportError(
            "Microsoft did not return account status and user type. "
            "Check User.Read.All and admin consent."
        )
    if value["accountEnabled"] is not True or value["userType"] != "Member":
        return None
    upn = str(value.get("userPrincipalName") or "").strip().casefold()
    email = str(value.get("mail") or upn).strip().casefold()
    domain = domain.strip().lstrip("@").casefold()
    if upn.rpartition("@")[2] != domain or email.rpartition("@")[2] != domain:
        return None
    try:
        object_id = str(UUID(str(value.get("id") or "")))
        code = str(value.get("employeeId") or "").strip()
        if not code:
            code = (
                "ENTRA-"
                + hashlib.sha256(f"{tenant}\0{object_id}".encode()).hexdigest()[:40].upper()
            )
        person = PersonUpsert(
            employee_code=code,
            display_name=value.get("displayName") or email,
            email=email,
            department=value.get("department"),
            roles=("employee",),
            active=True,
        )
        # Validate sign-in names even when the notification mailbox differs.
        PersonUpsert(employee_code=code, display_name=person.display_name, email=upn)
    except ValueError as error:
        raise EntraImportError(
            "An employee has invalid ID, name, email, department or employee-code data "
            "in Entra. Correct the source record and fetch again."
        ) from error
    return EntraEmployee(object_id, upn, person)


async def _require_administrator(session: AsyncSession, actor_id: UUID) -> None:
    authorized = await session.scalar(
        select(ApplicationUser.id)
        .join(UserRole, UserRole.user_id == ApplicationUser.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            ApplicationUser.id == actor_id,
            ApplicationUser.active.is_(True),
            Role.key == "administrator",
            Role.active.is_(True),
        )
    )
    if authorized is None:
        raise EntraImportError(
            "Only an active application administrator can import Entra employees."
        )


async def _find_user(session: AsyncSession, employee: EntraEmployee) -> ApplicationUser | None:
    from sqlalchemy import or_

    return cast(
        ApplicationUser | None,
        await session.scalar(
            select(ApplicationUser).where(
                or_(
                    ApplicationUser.entra_object_id == employee.object_id,
                    ApplicationUser.email.in_((employee.person.email, employee.upn)),
                )
            )
        ),
    )


async def _plan(
    session: AsyncSession, employees: tuple[EntraEmployee, ...], *, lock: bool = False
) -> tuple[EntraImportRow, ...]:
    statement = select(ApplicationUser).order_by(ApplicationUser.id)
    if lock:
        statement = statement.with_for_update()
    users = tuple(await session.scalars(statement))
    by_oid = {u.entra_object_id.casefold(): u for u in users if u.entra_object_id}
    by_email = {u.email.casefold(): u for u in users}
    by_code = {u.employee_code: u for u in users}
    assigned_ids = set(
        await session.scalars(
            select(UserRole.user_id)
            .join(Role, Role.id == UserRole.role_id)
            .where(Role.active.is_(True))
        )
    )
    addresses = Counter(address for e in employees for address in {e.upn, e.person.email})
    codes = Counter(e.person.employee_code for e in employees)
    rows: list[EntraImportRow] = []
    for employee in employees:
        person = employee.person
        matches = {
            u.id: u
            for u in (
                by_oid.get(employee.object_id),
                by_email.get(person.email),
                by_email.get(employee.upn),
            )
            if u is not None
        }
        existing = next(iter(matches.values()), None)
        if (
            len(matches) > 1
            or any(addresses[a] > 1 for a in {employee.upn, person.email})
            or (
                existing
                and existing.entra_object_id
                and existing.entra_object_id.casefold() != employee.object_id
            )
        ):
            action, detail = (
                "error",
                "Email or sign-in identity belongs to another person. Resolve it before importing.",
            )
        elif existing is None and (
            person.employee_code in by_code or codes[person.employee_code] > 1
        ):
            action, detail = (
                "error",
                "Employee code is already in use. Correct it in Entra or the directory.",
            )
        elif existing and (not existing.active or existing.id not in assigned_ids):
            action, detail = (
                "skip",
                "Existing account is inactive or has no active roles; left unchanged.",
            )
        elif existing is None:
            action, detail = "insert", "Add with Employee role; available before first sign-in."
        elif (
            existing.display_name,
            existing.email,
            existing.department,
            existing.entra_object_id,
        ) == (person.display_name, person.email, person.department, employee.object_id):
            action, detail = "unchanged", "Already up to date."
        else:
            action, detail = (
                "update",
                "Update profile and link Entra identity; "
                "keep roles, employee code and ICAI details.",
            )
        rows.append(EntraImportRow(employee, action, detail))
    return tuple(rows)
