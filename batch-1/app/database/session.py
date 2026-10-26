"""Async SQLAlchemy engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.database.url import ensure_sqlite_database_directory

SQLITE_BUSY_TIMEOUT_MS = 5_000


class DatabaseManager:
    """Own the async engine and create an isolated session per operation."""

    def __init__(self, settings: Settings) -> None:
        ensure_sqlite_database_directory(settings.database_url)
        url = make_url(settings.database_url)
        engine_options: dict[str, Any] = {
            "echo": settings.database_echo,
            "pool_pre_ping": True,
        }
        if url.get_backend_name() == "postgresql":
            engine_options.update(
                {
                    "pool_size": settings.database_pool_size,
                    "max_overflow": settings.database_max_overflow,
                    "pool_timeout": settings.database_pool_timeout_seconds,
                    "pool_recycle": settings.database_pool_recycle_seconds,
                }
            )
            if url.drivername == "postgresql+asyncpg":
                engine_options["connect_args"] = {
                    "server_settings": {
                        "application_name": "qc-report-automation",
                        "statement_timeout": str(settings.database_statement_timeout_ms),
                    }
                }
        self.engine: AsyncEngine = create_async_engine(
            settings.database_url,
            **engine_options,
        )
        if self.engine.dialect.name == "sqlite":
            self._configure_sqlite()

        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            autoflush=False,
            expire_on_commit=False,
        )

    def _configure_sqlite(self) -> None:
        @event.listens_for(self.engine.sync_engine, "connect")
        def set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
            del connection_record
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            finally:
                cursor.close()

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session without hiding commit/rollback decisions from callers."""

        async with self.session_factory() as session:
            yield session

    async def dispose(self) -> None:
        """Release all engine connections during application shutdown."""

        await self.engine.dispose()


database = DatabaseManager(Settings.from_environment())


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session from the application-wide database manager."""

    async with database.session() as session:
        yield session
