"""NiceGUI entry point for the connected QC Report Automation application."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

from nicegui import app, ui
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings
from app.database.health import check_database_connection
from app.database.session import database
from app.pages.account import register_account_routes
from app.pages.admin_audit_history import register_admin_audit_history_routes
from app.pages.admin_people import register_admin_people_routes
from app.pages.admin_roles import register_admin_role_routes
from app.pages.admin_templates import register_admin_template_routes
from app.pages.admin_workflows import register_admin_workflow_routes
from app.pages.dashboard import register_dashboard_routes
from app.pages.information import register_information_routes
from app.pages.notifications import register_notification_routes
from app.pages.report_preparation import register_report_preparation_routes
from app.pages.report_register import register_report_register_routes
from app.pages.reports import register_report_routes
from app.pages.sign_in import register_sign_in_routes
from app.services.graph_mail_service import MicrosoftGraphMailDispatcher

BASE_DIR = Path(__file__).resolve().parent
FAVICON_PATH = BASE_DIR / "static" / "icons" / "shield-check.svg"
SETTINGS = Settings.from_environment()
APPLICATION_PORT = int(os.getenv("QC_APP_PORT", "8080"))
APPLICATION_HOST = os.getenv("QC_APP_HOST", "127.0.0.1")
APPLICATION_SHOW_BROWSER = os.getenv("QC_APP_SHOW_BROWSER", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LAUNCHER_APPLICATION_ID = "qc-report-automation"
MAIL_DISPATCHER = MicrosoftGraphMailDispatcher(settings=SETTINGS)
MAIL_TASK: asyncio.Task[None] | None = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger(__name__)
app.add_static_files("/static", BASE_DIR / "static")
app.add_middleware(GZipMiddleware, minimum_size=1_024, compresslevel=5)


@app.middleware("http")
async def secure_responses_and_cache_static_assets(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Cache versioned assets aggressively and revalidate unversioned assets hourly."""

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), geolocation=(), microphone=()"
    if request.url.path.startswith("/static/") and 200 <= response.status_code < 300:
        versioned = request.query_params.get("v") is not None
        response.headers["Cache-Control"] = (
            "private, max-age=31536000, immutable" if versioned else "private, max-age=3600"
        )
    else:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/_qc/launcher-status")
def launcher_status() -> dict[str, str]:
    """Identify a ready local instance without disclosing configuration."""

    return {"application": LAUNCHER_APPLICATION_ID, "status": "ready"}


register_sign_in_routes()
register_dashboard_routes()
register_account_routes()
register_report_register_routes()
register_admin_audit_history_routes()
register_admin_people_routes()
register_admin_role_routes()
register_admin_workflow_routes()
register_admin_template_routes()
register_notification_routes()
register_report_preparation_routes()
register_report_routes()
register_information_routes()


async def _database_startup() -> None:
    global MAIL_TASK
    dialect = await check_database_connection()
    LOGGER.info("Database connection ready (%s)", dialect)
    MAIL_TASK = asyncio.create_task(MAIL_DISPATCHER.run())


async def _database_shutdown() -> None:
    MAIL_DISPATCHER.stop()
    if MAIL_TASK is not None:
        await MAIL_TASK
    await database.dispose()


app.on_startup(_database_startup)
app.on_shutdown(_database_shutdown)


def run() -> None:
    SETTINGS.require_safe_application_host(APPLICATION_HOST)
    ui.run(
        title="QC Report Automation",
        host=APPLICATION_HOST,
        port=APPLICATION_PORT,
        storage_secret=SETTINGS.require_storage_secret(),
        favicon=FAVICON_PATH,
        reload=False,
        show=APPLICATION_SHOW_BROWSER,
    )


if __name__ in {"__main__", "__mp_main__"}:
    run()
