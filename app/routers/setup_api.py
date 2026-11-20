from __future__ import annotations

import os
import platform
import shutil
import sys
import time
from importlib import metadata
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import __version__
from ..config import BASE_DIR, Settings
from ..executor import format_bytes, format_duration
from ..module_state import load_state
from ..templating import render

router = APIRouter()

STARTED_AT = time.time()
PACKAGES = ("fastapi", "uvicorn", "jinja2", "paramiko", "bcrypt")


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "not installed"


def fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if value is None or value == "":
        return "—"
    return str(value)


def secret(value: str) -> str:
    return "configured" if value else "not set"


def path_row(label_key: str, label: str, path: Path) -> dict[str, object]:
    try:
        exists = path.exists()
        is_dir = path.is_dir()
        readable = os.access(path, os.R_OK)
        writable = os.access(path, os.W_OK)
    except OSError:
        exists = is_dir = readable = writable = False
    return {
        "key": label_key,
        "label": label,
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "readable": readable,
        "writable": writable,
    }


def disk_usage(path: Path) -> dict[str, object] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    percent = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
    return {
        "total": format_bytes(usage.total),
        "used": format_bytes(usage.used),
        "free": format_bytes(usage.free),
        "percent": percent,
    }


def config_groups(settings: Settings) -> list[dict[str, object]]:
    def group(title_key: str, title: str, rows: list[tuple[str, str, object]]):
        return {
            "title_key": title_key,
            "title": title,
            "rows": [
                {"key": key, "label": label, "value": fmt(value)}
                for key, label, value in rows
            ],
        }

    return [
        group(
            "info.server",
            "Server",
            [
                ("info.host", "Bind address", settings.host),
                ("info.port", "Port", settings.port),
                ("info.root_path", "Root path", settings.root_path),
                ("info.trusted_proxy", "Trusted proxy", settings.trusted_proxy),
                ("info.debug", "Debug", settings.debug),
            ],
        ),
        group(
            "info.execution",
            "Execution",
            [
                ("info.mode", "Mode", settings.execution_mode),
                ("info.ssh_host", "SSH host", settings.ssh_host),
                ("info.ssh_port", "SSH port", settings.ssh_port),
                ("info.ssh_user", "SSH user", settings.ssh_user),
                ("info.ssh_password", "SSH password", secret(settings.ssh_password)),
            ],
        ),
        group(
            "info.auth",
            "Authentication",
            [
                ("info.login_required", "Login required", settings.login_required),
                ("info.login_user", "Login user", settings.login_user),
                ("info.login_password", "Login password", secret(settings.login_pass)),
                ("info.check_system_user", "Check system user", settings.login_check_system_user),
                ("info.totp", "TOTP secret", secret(settings.login_totp_secret)),
                ("info.open_access", "Unauthenticated access", settings.allow_unauthenticated),
                ("info.update_key", "Update key", secret(settings.update_key)),
                ("info.basic_auth", "Basic Auth", settings.basic_auth),
                ("info.basic_user", "Basic Auth user", settings.basic_auth_user),
                ("info.basic_password", "Basic Auth password", secret(settings.basic_auth_pass)),
            ],
        ),
        group(
            "info.session",
            "Session",
            [
                ("info.idle_timeout", "Idle timeout", settings.session_idle_timeout),
                ("info.absolute_timeout", "Absolute timeout", settings.session_absolute_timeout),
                ("info.secure_cookie", "Secure cookie", settings.session_cookie_secure),
                ("info.max_failures", "Max login failures", settings.login_max_failures),
                ("info.failure_window", "Failure window", settings.login_failure_window),
                ("info.lockout", "Lockout time", settings.login_lockout_time),
            ],
        ),
        group(
            "info.alerts",
            "Alerts",
            [
                ("info.ntfy", "ntfy URL", "configured" if settings.alert_ntfy_url else "not set"),
                ("info.telegram_token", "Telegram token", secret(settings.alert_telegram_token)),
                ("info.telegram_chat", "Telegram chat", secret(settings.alert_telegram_chat)),
                ("info.temp_threshold", "Temperature threshold", settings.alert_temp_threshold),
                ("info.disk_threshold", "Disk threshold", settings.alert_disk_threshold),
            ],
        ),
        group(
            "info.ui",
            "Interface",
            [
                ("info.default_lang", "Default language", settings.default_lang),
                ("info.dashboard_services", "Dashboard services", ", ".join(settings.dashboard_services)),
                ("info.api_allow_ips", "API allow-list", ", ".join(settings.api_allow_ips) or "(any)"),
            ],
        ),
    ]


def module_states(settings: Settings) -> list[dict[str, object]]:
    overrides = load_state(settings)
    modules = [
        {
            "key": module.key,
            "title": module.title,
            "group": module.group,
            "enabled": module.enabled,
            "overridden": module.key in overrides,
        }
        for module in settings.modules.values()
    ]
    modules.sort(key=lambda row: (str(row["group"]).lower(), str(row["key"])))
    return modules


@router.get("/system-info", response_class=HTMLResponse)
def system_info_page(request: Request):
    settings = request.app.state.settings

    runtime = [
        {"key": "info.version", "label": "PiWeb version", "value": __version__},
        {"key": "info.python", "label": "Python version", "value": platform.python_version()},
        {"key": "info.implementation", "label": "Implementation", "value": platform.python_implementation()},
        {"key": "info.executable", "label": "Python executable", "value": sys.executable},
        {"key": "info.platform", "label": "Platform", "value": platform.platform()},
        {"key": "info.machine", "label": "Machine", "value": platform.machine()},
        {"key": "info.processor", "label": "Processor", "value": platform.processor() or "—"},
        {"key": "info.pid", "label": "Process ID", "value": str(os.getpid())},
        {"key": "info.cwd", "label": "Working directory", "value": os.getcwd()},
        {
            "key": "info.start",
            "label": "Application start",
            "value": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(STARTED_AT)),
        },
        {
            "key": "info.uptime",
            "label": "Uptime",
            "value": format_duration(time.time() - STARTED_AT),
        },
        {"key": "info.mode", "label": "Execution mode", "value": settings.execution_mode},
    ]

    packages = [
        {"name": name, "version": package_version(name)} for name in PACKAGES
    ]

    paths = [
        path_row("info.repo", "Repository", BASE_DIR),
        path_row("info.config", "Configuration", settings.config_path or BASE_DIR / "config.toml"),
        path_row("info.data", "Data", settings.data_dir),
        path_row("info.buttons", "Buttons", settings.buttons_dir),
        path_row("info.scripts", "Scripts", settings.scripts_dir),
        path_row("info.logs", "Command logs", settings.command_logs_dir),
        path_row("info.backups", "Backups", settings.backups_dir),
    ]

    return render(
        request,
        "runtime_info.html",
        runtime=runtime,
        packages=packages,
        paths=paths,
        disk=disk_usage(BASE_DIR),
        config_groups=config_groups(settings),
        modules=module_states(settings),
    )
