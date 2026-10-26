"""Establish the database migration baseline.

Revision ID: 0001_database_foundation
Revises:
Create Date: 2026-07-15
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_database_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Mark the database foundation revision.

    Business tables are intentionally introduced by later feature migrations.
    """


def downgrade() -> None:
    """Remove the baseline revision marker."""
