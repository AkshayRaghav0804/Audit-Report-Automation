"""Windows-friendly local launcher for the QC Automation NiceGUI app."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from dotenv import dotenv_values

from scripts.local_preflight import (
    AUTHENTICATION_FAILURE_EXIT_CODE,
    PreflightError,
    database_target,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
LOCAL_ENVIRONMENT_FILES = (
    PROJECT_ROOT / ".env",
    PROJECT_ROOT / ".env.local",
    PROJECT_ROOT / ".env.postgres.local",
)
DEFAULT_APP_PORT = 8080
APP_HOST = "127.0.0.1"
APP_BROWSER_HOST = "localhost"
APP_ID = "qc-report-automation"
STATUS_PATH = "/_qc/launcher-status"
STARTUP_TIMEOUT_SECONDS = 30.0
LOCAL_POSTGRES_PROVISIONER = PROJECT_ROOT / "scripts" / "provision_user_postgres.ps1"
NON_RUNTIME_SECRET_NAMES = frozenset(
    {
        "QC_AZURE_DATABASE_URL",
        "QC_POSTGRES_ADMIN_PASSWORD",
        "QC_POSTGRES_APP_PASSWORD",
        "QC_POSTGRES_TEST_URL",
    }
)


class LauncherError(RuntimeError):
    """An expected, user-actionable local launcher failure."""


class AuthenticationFailure(LauncherError):
    """A database login failure which must not trigger automatic provisioning."""


class PortState(Enum):
    AVAILABLE = "available"
    QC_APP = "qc_app"
    OTHER_PROCESS = "other_process"


@dataclass(frozen=True, slots=True)
class LocalPostgresRecovery:
    """Validated parameters for this repository's current-user cluster."""

    port: int
    major_version: str


def load_local_environment(
    base_environment: Mapping[str, str] | None = None,
    paths: Sequence[Path] = LOCAL_ENVIRONMENT_FILES,
) -> tuple[dict[str, str], tuple[Path, ...]]:
    """Layer ignored dotenv files without logging any setting values.

    Later files override earlier files, while an explicitly supplied process
    environment has final precedence.
    """

    loaded_values: dict[str, str] = {}
    loaded_paths: list[Path] = []
    for path in paths:
        if not path.is_file():
            continue
        loaded_paths.append(path)
        for name, value in dotenv_values(path).items():
            if value is not None:
                loaded_values[name] = value

    environment = loaded_values
    environment.update(dict(os.environ if base_environment is None else base_environment))
    return environment, tuple(loaded_paths)


def configured_port(environment: Mapping[str, str]) -> int:
    raw_port = environment.get("QC_APP_PORT", str(DEFAULT_APP_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as error:
        raise LauncherError("QC_APP_PORT must be an integer from 1 through 65535.") from error
    if not 1 <= port <= 65_535:
        raise LauncherError("QC_APP_PORT must be an integer from 1 through 65535.")
    return port


def remove_non_runtime_secrets(environment: dict[str, str]) -> None:
    """Keep provisioning and test credentials out of launched processes."""

    for name in NON_RUNTIME_SECRET_NAMES:
        environment.pop(name, None)


def app_url(port: int) -> str:
    return f"http://{APP_HOST}:{port}"


def browser_url(port: int) -> str:
    """Use the same origin as the registered local Entra redirect URI."""

    return f"http://{APP_BROWSER_HOST}:{port}"


def is_qc_app_ready(url: str, *, timeout: float = 0.75) -> bool:
    request = urllib.request.Request(
        f"{url}{STATUS_PATH}",
        headers={"Accept": "application/json"},
    )
    try:
        # Callers supply app_url(), whose host is the fixed loopback APP_HOST constant.
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310
            payload = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return False
    return isinstance(payload, dict) and payload.get("application") == APP_ID


def classify_port(port: int) -> PortState:
    url = app_url(port)
    if is_qc_app_ready(url):
        return PortState.QC_APP

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        exclusive_address_use = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive_address_use is not None:
            connection.setsockopt(socket.SOL_SOCKET, exclusive_address_use, 1)
        try:
            connection.bind((APP_HOST, port))
        except OSError:
            return PortState.OTHER_PROCESS
    return PortState.AVAILABLE


def require_local_settings(environment: Mapping[str, str]) -> None:
    if not environment.get("NICEGUI_STORAGE_SECRET", "").strip():
        raise LauncherError(
            "NICEGUI_STORAGE_SECRET is missing. Add a long random value to .env.local; "
            "the launcher never prints it."
        )


def run_preflight(environment: Mapping[str, str]) -> None:
    completed = subprocess.run(
        [str(VENV_PYTHON), "-m", "scripts.local_preflight"],
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode == AUTHENTICATION_FAILURE_EXIT_CODE:
        raise AuthenticationFailure(
            "PostgreSQL authentication failed; automatic restart was skipped. "
            "Check for a local port conflict or mismatched saved credentials. "
            "See docs/postgresql-migration.md."
        )
    if completed.returncode != 0:
        raise LauncherError("Local startup checks failed; the application was not started.")


def local_postgres_recovery(
    environment: Mapping[str, str],
    *,
    project_root: Path = PROJECT_ROOT,
) -> LocalPostgresRecovery | None:
    """Recognize the existing project-owned cluster without exposing URL credentials."""

    raw_port = environment.get("QC_POSTGRES_LOCAL_PORT", "").strip()
    database_url = environment.get("QC_DATABASE_URL", "").strip()
    try:
        saved_port = int(raw_port)
        target = database_target(database_url)
    except (PreflightError, TypeError, ValueError):
        return None

    if not 1 <= saved_port <= 65_535:
        return None
    if target.host not in {"127.0.0.1", "localhost", "::1"}:
        return None
    if target.port != saved_port or target.database != "qc_automation":
        return None

    version_path = project_root / "data" / "postgres-local" / "PG_VERSION"
    provisioner_path = project_root / "scripts" / "provision_user_postgres.ps1"
    if not version_path.is_file() or not provisioner_path.is_file():
        return None
    try:
        major_version = version_path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        return None
    if not major_version.isdigit():
        return None
    return LocalPostgresRecovery(port=saved_port, major_version=major_version)


def repair_command(recovery: LocalPostgresRecovery) -> tuple[str, ...]:
    """Build a credential-free repair command for display and execution."""

    return (
        "powershell.exe",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(LOCAL_POSTGRES_PROVISIONER),
        "-Port",
        str(recovery.port),
        "-PostgresMajorVersion",
        recovery.major_version,
    )


def display_repair_command(recovery: LocalPostgresRecovery) -> str:
    return (
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass "
        "-File .\\scripts\\provision_user_postgres.ps1 "
        f"-Port {recovery.port} -PostgresMajorVersion {recovery.major_version}"
    )


def ensure_postgres_ready(environment: Mapping[str, str]) -> None:
    """Run the preflight and restart only a positively identified local cluster."""

    try:
        run_preflight(environment)
        return
    except AuthenticationFailure:
        raise
    except LauncherError as preflight_error:
        recovery = local_postgres_recovery(environment)
        if recovery is None:
            raise preflight_error

    print(
        "The project-managed PostgreSQL cluster is unavailable; "
        f"attempting a safe restart on 127.0.0.1:{recovery.port}."
    )
    completed = subprocess.run(
        repair_command(recovery),
        cwd=PROJECT_ROOT,
        env=environment,
        check=False,
    )
    if completed.returncode != 0:
        raise LauncherError(
            "Automatic PostgreSQL recovery failed. Run this credential-free repair command "
            f"from the project directory:\n  {display_repair_command(recovery)}"
        )

    print("PostgreSQL recovery completed; verifying the application database.")
    try:
        run_preflight(environment)
    except LauncherError as error:
        raise LauncherError(
            "PostgreSQL recovery ran, but the database readiness check still failed. "
            "Run this credential-free repair command from the project directory:\n  "
            f"{display_repair_command(recovery)}"
        ) from error


def open_browser(url: str) -> None:
    print(f"Opening {url}")
    try:
        opened = webbrowser.open(url, new=2)
    except (OSError, webbrowser.Error) as error:
        print(
            f"The browser could not be opened automatically ({type(error).__name__}). "
            f"Open {url} manually."
        )
        return
    if not opened:
        print(f"The browser could not be opened automatically. Open {url} manually.")


def wait_until_ready(process: subprocess.Popen[bytes], url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        return_code = process.poll()
        if return_code is not None:
            raise LauncherError(
                f"The NiceGUI process exited before it became ready (exit code {return_code})."
            )
        if is_qc_app_ready(url):
            return
        time.sleep(0.25)
    raise LauncherError(
        f"The NiceGUI app did not become ready at {url} within "
        f"{STARTUP_TIMEOUT_SECONDS:.0f} seconds."
    )


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def launch() -> int:
    if Path(sys.executable).resolve() != VENV_PYTHON.resolve():
        raise LauncherError(
            f"Run {PROJECT_ROOT / 'start-local.cmd'} so the project virtual environment is used."
        )

    environment, loaded_paths = load_local_environment()
    if loaded_paths:
        names = ", ".join(path.name for path in loaded_paths)
        print(f"Loaded local settings from {names} (values hidden).")
    else:
        print("No local dotenv files were found; using the current process environment.")
    remove_non_runtime_secrets(environment)

    port = configured_port(environment)
    readiness_url = app_url(port)
    url = browser_url(port)
    environment["QC_APP_PORT"] = str(port)
    environment["QC_APP_HOST"] = APP_HOST
    environment["QC_APP_SHOW_BROWSER"] = "false"
    environment["QC_APPLICATION_BASE_URL"] = url

    port_state = classify_port(port)
    if port_state is PortState.QC_APP:
        ensure_postgres_ready(environment)
        print(f"QC Automation is already running at {url}; leaving it running.")
        open_browser(url)
        return 0
    if port_state is PortState.OTHER_PROCESS:
        raise LauncherError(
            f"Port {port} is already used by another process. It was not stopped. "
            "Close that process or set QC_APP_PORT to another port in .env.local."
        )

    require_local_settings(environment)
    ensure_postgres_ready(environment)

    print(f"Starting QC Automation at {url} with {VENV_PYTHON}.")
    process = subprocess.Popen(
        [str(VENV_PYTHON), "-m", "app.main"],
        cwd=PROJECT_ROOT,
        env=environment,
    )
    try:
        wait_until_ready(process, readiness_url)
        open_browser(url)
        print("QC Automation is ready. Press Ctrl+C to stop it.")
        return process.wait()
    except KeyboardInterrupt:
        print("\nStopping QC Automation...")
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stop_process(process)
            return 0
    except BaseException:
        stop_process(process)
        raise


def main() -> int:
    try:
        return launch()
    except LauncherError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
