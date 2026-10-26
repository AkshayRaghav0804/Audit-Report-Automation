"""Database readiness checks used by application startup."""

from __future__ import annotations

from sqlalchemy import text

from app.database.session import DatabaseManager, database


async def check_database_connection(manager: DatabaseManager = database) -> str:
    """Execute a minimal query and return the connected database dialect."""

    async with manager.engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            raise RuntimeError("Database readiness query returned an unexpected result.")
    return manager.engine.dialect.name
