"""Secret-safe PostgreSQL preflight for the local Windows launcher."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

from asyncpg.exceptions import InvalidAuthorizationSpecificationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import Settings

CONNECTION_TIMEOUT_SECONDS = 8.0
AUTHENTICATION_FAILURE_EXIT_CODE = 2


class PreflightError(RuntimeError):
    """A local configuration or connectivity failure safe to show to a user."""


class PostgresAuthenticationError(PreflightError):
    """A responding server rejected the configured login."""


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    host: str
    port: int
    database: str

    def __str__(self) -> str:
        return f'{self.host}:{self.port} (database "{self.database}")'


def database_target(database_url: str) -> DatabaseTarget:
    try:
        url = make_url(database_url)
    except Exception as error:
        raise PreflightError("QC_DATABASE_URL is not a valid SQLAlchemy database URL.") from error
    if url.get_backend_name() != "postgresql":
        raise PreflightError("QC_DATABASE_URL must select PostgreSQL for local startup.")
    return DatabaseTarget(
        host=url.host or "localhost",
        port=url.port or 5432,
        database=url.database or "postgres",
    )


async def check_local_postgres(settings: Settings) -> DatabaseTarget:
    target = database_target(settings.database_url)
    engine: AsyncEngine | None = None
    try:
        try:
            engine = create_async_engine(
                settings.database_url,
                pool_pre_ping=True,
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_max_overflow,
                pool_timeout=settings.database_pool_timeout_seconds,
                pool_recycle=settings.database_pool_recycle_seconds,
                connect_args={
                    "server_settings": {
                        "application_name": "qc-local-launcher",
                        "statement_timeout": str(settings.database_statement_timeout_ms),
                    }
                },
            )
            await asyncio.wait_for(
                _execute_readiness_query(engine),
                timeout=CONNECTION_TIMEOUT_SECONDS,
            )
        except InvalidAuthorizationSpecificationError as error:
            raise PostgresAuthenticationError(
                f"PostgreSQL rejected the configured credentials at {target}. "
                "The port may belong to another PostgreSQL instance. "
                "Check the listener and saved settings; restarting will not fix authentication. "
                "See docs/postgresql-migration.md for local port conflicts."
            ) from error
        except TimeoutError as error:
            raise PreflightError(
                f"PostgreSQL did not respond at {target} within "
                f"{CONNECTION_TIMEOUT_SECONDS:.0f} seconds."
            ) from error
        except Exception as error:
            raise PreflightError(
                f"PostgreSQL is not reachable at {target} "
                f"({type(error).__name__}). Check the service and local credentials."
            ) from error
        return target
    finally:
        if engine is not None:
            await engine.dispose()


async def _execute_readiness_query(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            raise PreflightError("PostgreSQL returned an unexpected readiness result.")


async def async_main() -> int:
    settings = Settings.from_environment()
    settings.require_storage_secret()
    target = await check_local_postgres(settings)
    print(f"PostgreSQL is reachable at {target}.")
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except (PreflightError, RuntimeError, ValueError) as error:
        if isinstance(error, PreflightError):
            message = str(error)
        elif "NICEGUI_STORAGE_SECRET" in str(error):
            message = (
                "NICEGUI_STORAGE_SECRET is missing. Add a long random value to .env.local; "
                "the value will remain hidden."
            )
        else:
            message = f"Local configuration is invalid ({type(error).__name__})."
        print(f"ERROR: {message}", file=sys.stderr)
        if isinstance(error, PostgresAuthenticationError):
            return AUTHENTICATION_FAILURE_EXIT_CODE
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
