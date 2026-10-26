"""Helpers for database URL preparation."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url


def ensure_sqlite_database_directory(database_url: str) -> None:
    """Create the parent directory for a file-backed SQLite database."""

    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return
    if not url.database or url.database == ":memory:":
        return

    Path(url.database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
