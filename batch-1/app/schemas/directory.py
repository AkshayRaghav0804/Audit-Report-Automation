"""Validated directory and bulk-import data transfer objects."""

from __future__ import annotations

import re
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ImportRowAction(StrEnum):
    INSERT = "insert"
    UPDATE = "update"
    UNCHANGED = "unchanged"
    ERROR = "error"


class PersonUpsert(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    employee_code: str = Field(min_length=1, max_length=50)
    display_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    department: str | None = Field(default=None, max_length=120)
    roles: tuple[str, ...] = ()
    icai_membership_number: str | None = Field(default=None, max_length=50)
    active: bool = True

    @field_validator("employee_code")
    @classmethod
    def normalize_employee_code(cls, value: str) -> str:
        return value.upper()

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.casefold()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
            raise ValueError("Enter a valid email address.")
        return normalized

    @field_validator("roles")
    @classmethod
    def normalize_roles(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(role.strip().casefold().replace(" ", "_") for role in value))

    @field_validator("icai_membership_number")
    @classmethod
    def normalize_icai_number(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = re.sub(r"\s+", "", value)
        if not re.fullmatch(r"\d{4,10}", normalized):
            raise ValueError("ICAI membership number must contain 4 to 10 digits.")
        return normalized


class DirectoryPerson(BaseModel):
    id: UUID
    employee_code: str
    display_name: str
    email: str
    department: str | None
    roles: tuple[str, ...]
    icai_membership_number: str | None
    active: bool

    @property
    def selection_label(self) -> str:
        role_label = ", ".join(role.replace("_", " ").title() for role in self.roles)
        suffix = role_label or self.employee_code
        return f"{self.display_name} — {suffix} — {self.email}"


class ImportIssue(BaseModel):
    row_number: int
    field_name: str | None = None
    code: str
    message: str
    blocking: bool = True


class PeopleImportRow(BaseModel):
    row_number: int
    person: PersonUpsert | None
    action: ImportRowAction
    issues: tuple[ImportIssue, ...] = ()


class PeopleImportPreview(BaseModel):
    filename: str
    file_sha256: str
    rows: tuple[PeopleImportRow, ...]

    @property
    def inserted_count(self) -> int:
        return sum(row.action is ImportRowAction.INSERT for row in self.rows)

    @property
    def updated_count(self) -> int:
        return sum(row.action is ImportRowAction.UPDATE for row in self.rows)

    @property
    def unchanged_count(self) -> int:
        return sum(row.action is ImportRowAction.UNCHANGED for row in self.rows)

    @property
    def error_count(self) -> int:
        return sum(issue.blocking for row in self.rows for issue in row.issues)

    @property
    def warning_count(self) -> int:
        return sum(not issue.blocking for row in self.rows for issue in row.issues)

    @property
    def can_commit(self) -> bool:
        return self.error_count == 0
