"""Application user and permission data."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class User:
    id: str
    name: str
    email: str
    role: str
    permissions: frozenset[str]

    @property
    def initials(self) -> str:
        parts = self.name.split()
        return "".join(part[0] for part in parts[:2]).upper()
