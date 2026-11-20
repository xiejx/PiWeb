from __future__ import annotations

import importlib.util
import os
import platform
import re
import socket
import sys
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..alerts import configured as alerts_configured
from ..alerts import send_alert
from ..executor import environment_ok, get_executor
from ..security import (
    auth_configured,
    auth_log,
    auth_log_recent,
    client_ip,
    default_credentials,
    open_mode,
    totp_match,
    totp_new_secret,
    totp_provisioning_uri,
)
from ..system import pi_model
from ..templating import render

router = APIRouter()

REQUIRED_PACKAGES = ("fastapi", "uvicorn", "jinja2", "paramiko")

DIR_FIXES = {
    "scripts_dir": "scripts_dir",
    "buttons_dir": "buttons_dir",
    "logs_dir": "command_logs_dir",
    "data_dir": "data_dir",
}

AUTH_LABELS = {
    "login_ok": ("success", "Signed in"),
    "login_failed": ("danger", "Failed attempt"),
    "locked": ("warning", "Locked out"),
    "expired": ("secondary", "Session expired"),
    "logout": ("secondary", "Signed out"),
}

ALERT_TEXT = "PiWeb test alert"


def _check(
    ok: bool,
    label: str,
    detail: str = "",
    fix: str = "",
    informational: bool = False,
) -> dict[str, object]:
    return {
        "ok": bool(ok),
        "label": label,
        "detail": detail,
        "fix": fix,
        "informational": informational,
    }


# ── Individual check groups ─────────────────────────────────────────────────

def _runtime_checks() -> list[dict[str, object]]:
    version_ok = sys.version_info >= (3, 11)
    missing = []
    for package in REQUIRED_PACKAGES:
        try:
            found = importlib.util.find_spec(package) is not None
        except (ImportError, ValueError):
            found = False
        if not found:
            missing.append(package)

    return [
        _check(
            version_ok,
            "Python version",
            f"Python {platform.python_version()} (minimum 3.11 required)",
        ),
        _check(
            not missing,
            "Required Python packages",
            "all importable: " + ", ".join(REQUIRED_PACKAGES)
            if not missing
            else "missing: " + ", ".join(missing),
        ),
    ]


def _security_checks(settings) -> list[dict[str, object]]:
    button_api_on = settings.module_enabled("button_api")
    restricted = bool(settings.api_allow_ips)
    button_ok = (not button_api_on) or restricted
    if not button_api_on:
        button_detail = "the Button API is disabled"
    elif restricted:
        button_detail = "enabled, limited to: " + ", ".join(settings.api_allow_ips)
    else:
        button_detail = (
            "ENABLED for any address — the API runs a button's command without "
            "a login when given its hash. Set ui.api_allow_ips in config.toml, "
            "or switch the module off."
        )

    return [
        _check(
            auth_configured(settings),
            "Authentication configured",
            "login page and/or HTTP Basic Auth is enabled"
            if auth_configured(settings)
            else "NO authentication — anyone reaching this panel can run commands",
        ),
        _check(
            not open_mode(settings),
            "Unauthenticated access not allowed",
            "allow_unauthenticated is true — the panel is open to everyone"
            if open_mode(settings)
            else "open access is disabled",
        ),
        _check(
            not default_credentials(settings),
            "Credentials changed from defaults",
            "still using a shipped default password — change it in config.toml"
            if default_credentials(settings)
            else "not using the shipped defaults",
        ),
        _check(button_ok, "Button API is off, or restricted by IP", button_detail),
    ]


def _directory_checks(settings) -> list[dict[str, object]]:
    entries = (
        ("user_scripts/", settings.scripts_dir, "scripts_dir",
         "the Script Editor cannot save"),
        ("buttons/", settings.buttons_dir, "buttons_dir",
         "buttons cannot be saved"),
        ("command_logs/", settings.command_logs_dir, "logs_dir",
         "background commands will fail"),
        ("data/", settings.data_dir, "data_dir",
         "history and session data cannot be written"),
    )
    checks = []
    for label, path, fix, consequence in entries:
        exists = path.is_dir()
        writable = exists and os.access(path, os.W_OK)
        if not exists:
            detail = f"{path} is missing"
        elif not writable:
            detail = f"{path} is not writable — {consequence}"
        else:
            detail = f"{path} (writable)"
        checks.append(
            _check(exists and writable, f"{label} exists and is writable",
                   detail, fix)
        )
    return checks


def _config_checks(settings) -> list[dict[str, object]]:
    path = settings.config_path
    readable = bool(path) and path.is_file() and os.access(path, os.R_OK)
    return [
        _check(
            readable,
            "config.toml readable",
            str(path) if readable else f"{path or 'config.toml'} cannot be read "
                                     "— PiWeb will not start",
        ),
        _check(
            bool(settings.session_secret),
            "Session secret present",
            "session secret is configured"
            if settings.session_secret
            else "missing — sessions cannot be signed",
        ),
    ]


def _execution_checks(settings, executor) -> list[dict[str, object]]:
    if settings.execution_mode == "ssh":
        try:
            with socket.create_connection(
                (settings.ssh_host, settings.ssh_port), timeout=3
            ):
                reachable = True
            detail = (
                f"SSH endpoint reachable at "
                f"{settings.ssh_host}:{settings.ssh_port}"
            )
        except OSError as exc:
            reachable = False
            detail = (
                f"cannot reach {settings.ssh_host}:{settings.ssh_port} — {exc}"
            )
        return [
            _check(
                reachable,
                "SSH connection to the Pi",
                detail,
                "start_ssh" if not reachable else "",
            )
        ]

    result = executor.run("true", timeout=5)
    local_ok = environment_ok() and result.ok
    return [
        _check(
            local_ok,
            "Local execution ready",
            "direct local execution is available"
            if local_ok
            else "local execution is not available (Linux /proc not found)",
        )
    ]


def _gpio_checks(executor) -> list[dict[str, object]]:
    model = pi_model().strip()
    is_pi5 = "raspberry pi 5" in model.lower()
    command = "raspi-gpio" if is_pi5 else "gpio"
    installed = executor.command_exists(command)

    if is_pi5:
        label = "raspi-gpio (required on Pi 5)"
        detail = "installed" if installed else "missing"
        fix = "" if installed else "install_raspi_gpio"
    else:
        label = "WiringPi gpio command (required on Pi 1–4)"
        detail = "installed" if installed else "missing — the GPIO page will not work"
        fix = ""

    return [
        _check(bool(model), "Hardware", model or "Unknown (not a Raspberry Pi?)"),
        _check(installed, label, detail, fix),
    ]


def _system_command_checks(executor) -> list[dict[str, object]]:
    checks = []
    for command in ("ps", "who", "df"):
        installed = executor.command_exists(command)
        checks.append(
            _check(installed, command, "installed" if installed else "missing")
        )

    service_ok = executor.command_exists("systemctl") or executor.command_exists(
        "service"
    )
    checks.append(
        _check(
            service_ok,
            "service / systemctl",
            "available" if service_ok else "missing",
        )
    )
    return checks


def _web_server_checks(executor) -> list[dict[str, object]]:
    installed = executor.command_exists("nginx")
    running = False
    if installed:
        result = executor.run("systemctl is-active nginx 2>/dev/null", timeout=5)
        running = result.stdout.strip() == "active"

    if not installed:
        detail = "nginx is not installed (informational)"
    elif running:
        detail = "nginx is installed and active (informational)"
    else:
        detail = "nginx is installed but not running (informational)"

    return [
        _check(
            installed and running,
            "nginx web server",
            detail,
            informational=True,
        )
    ]


def build_sections(settings, executor) -> list[dict[str, object]]:
    return [
        {"key": "runtime", "title": "Runtime", "checks": _runtime_checks()},
        {"key": "security", "title": "Security", "checks": _security_checks(settings)},
        {"key": "directories", "title": "Directories",
         "checks": _directory_checks(settings)},
        {"key": "config", "title": "Config", "checks": _config_checks(settings)},
        {"key": "execution", "title": "Execution",
         "checks": _execution_checks(settings, executor)},
        {"key": "gpio", "title": "GPIO", "checks": _gpio_checks(executor)},
        {"key": "commands", "title": "System commands",
         "checks": _system_command_checks(executor)},
        {"key": "web", "title": "Web server", "checks": _web_server_checks(executor)},
    ]


def _failing_count(sections: list[dict[str, object]]) -> int:
    return sum(
        1
        for section in sections
        for check in section["checks"]
        if not check["ok"] and not check["informational"]
    )


def _auth_events(settings) -> list[dict[str, str]]:
    events = []
    for event in auth_log_recent(settings, 10):
        tone, label = AUTH_LABELS.get(
            event["event"], ("secondary", event["event"])
        )
        events.append({**event, "tone": tone, "label": label})
    return events


# ── Alerts ──────────────────────────────────────────────────────────────────

def _alerts_configured(settings) -> bool:
    return alerts_configured(settings)


def send_test_alert(settings) -> tuple[bool, str]:
    if not alerts_configured(settings):
        return False, (
            "No alert channel is configured — set alerts.ntfy_url or "
            "alerts.telegram_token + alerts.telegram_chat in config.toml"
        )
    ok, detail = send_alert(settings, "PiWeb test alert", ALERT_TEXT)
    if not ok:
        return False, (
            detail + " — check the URL/token and that this Pi has internet access"
        )
    return True, "Test alert " + detail


# ── TOTP persistence ────────────────────────────────────────────────────────

def _config_path(settings) -> Path:
    if settings.config_path is not None:
        return Path(settings.config_path)
    return settings.data_dir.parent / "config.toml"


def _write_totp_secret(path: Path, secret: str) -> None:
    """Rewrite only auth.totp_secret, keeping every other config line."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f'totp_secret = "{secret}"'

    if not text.strip():
        text = "# PiWeb configuration\n[auth]\n" + line + "\n"
    else:
        section_re = re.compile(r"(?ms)^\[auth\]\s*$(.*?)(?=^\[|\Z)")
        match = section_re.search(text)
        if match:
            body = re.sub(
                r"(?m)^\s*totp_secret\s*=.*\n?", "", match.group(1)
            ).rstrip("\n")
            tail = text[match.end(1) :]
            separator = "\n" if not tail or tail.startswith("\n") else "\n\n"
            text = (
                text[: match.start(1)]
                + body
                + "\n"
                + line
                + separator
                + tail
            )
        else:
            text = text.rstrip() + "\n\n[auth]\n" + line + "\n"

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── Request body helper ─────────────────────────────────────────────────────

async def _payload(request: Request) -> dict[str, object]:
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except (ValueError, AttributeError, RuntimeError):
            return {}
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - malformed body
        return {}
    return dict(form)


# ── Page ────────────────────────────────────────────────────────────────────

@router.get("/check", response_class=HTMLResponse)
def check_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    sections = build_sections(settings, executor)
    return render(
        request,
        "check.html",
        sections=sections,
        fail_count=_failing_count(sections),
        python_version=platform.python_version(),
        auth_events=_auth_events(settings),
        totp_on=bool(settings.login_totp_secret),
        alerts_on=_alerts_configured(settings),
        alert_temp=settings.alert_temp_threshold,
        alert_disk=settings.alert_disk_threshold,
    )


# ── Fixes and re-check ──────────────────────────────────────────────────────

@router.post("/ajax/check/fix")
async def check_fix(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)
    key = str(data.get("fix", ""))

    if key in DIR_FIXES:
        path = getattr(settings, DIR_FIXES[key])
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, 0o755)
        except OSError as exc:
            return JSONResponse(
                {"ok": False, "output": f"Could not prepare {path}: {exc}"}
            )
        ok = True
        output = f"Created {path} with mode 755."
    elif key == "install_raspi_gpio":
        result = executor.run("sudo apt-get install -y raspi-gpio", timeout=300)
        ok = result.ok
        output = result.output or "(no output)"
    elif key == "start_ssh":
        result = executor.run(
            "sudo systemctl enable --now ssh 2>&1", timeout=60
        )
        ok = result.ok
        output = result.output or "(no output)"
    else:
        return JSONResponse(
            {"ok": False, "error": f"Unknown fix key: {key}"}, status_code=400
        )

    auth_log(
        settings,
        "check_fix",
        f"{key} ({'ok' if ok else 'failed'})",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": ok, "output": output})


@router.post("/ajax/check/rerun")
def check_rerun(request: Request):
    return JSONResponse({"ok": True})


# ── Two-factor authentication ───────────────────────────────────────────────

@router.post("/ajax/check/totp/start")
def totp_start(request: Request):
    settings = request.app.state.settings
    if settings.login_totp_secret:
        return JSONResponse(
            {"ok": False, "error": "Two-factor login is already enabled"},
            status_code=400,
        )

    secret = totp_new_secret()
    request.session["totp_pending"] = secret
    uri = totp_provisioning_uri(settings, secret)
    auth_log(
        settings,
        "totp",
        "two-factor setup started",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "secret": secret, "uri": uri})


@router.post("/ajax/check/totp/enable")
async def totp_enable(request: Request):
    settings = request.app.state.settings
    data = await _payload(request)
    code = str(data.get("code", ""))
    pending = str(request.session.get("totp_pending", ""))

    if not pending:
        return JSONResponse(
            {"ok": False, "error": "Start the setup again"}, status_code=400
        )
    if totp_match(pending, code) == 0:
        return JSONResponse(
            {
                "ok": False,
                "error": "That code does not match — check the time on this Pi "
                         "and on your phone",
            },
            status_code=400,
        )

    try:
        _write_totp_secret(_config_path(settings), pending)
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Could not write config.toml: {exc}"},
            status_code=500,
        )

    settings.login_totp_secret = pending
    request.session.pop("totp_pending", None)
    auth_log(
        settings,
        "totp",
        "two-factor login enabled",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse(
        {
            "ok": True,
            "message": "Two-factor login is on. You will be asked for a code "
                       "at the next sign-in.",
        }
    )


@router.post("/ajax/check/totp/disable")
async def totp_disable(request: Request):
    settings = request.app.state.settings
    if not settings.login_totp_secret:
        return JSONResponse(
            {"ok": False, "error": "Two-factor login is not enabled"},
            status_code=400,
        )

    data = await _payload(request)
    code = str(data.get("code", ""))
    if totp_match(settings.login_totp_secret, code) == 0:
        return JSONResponse(
            {"ok": False, "error": "That code does not match"}, status_code=400
        )

    try:
        _write_totp_secret(_config_path(settings), "")
    except OSError as exc:
        return JSONResponse(
            {"ok": False, "error": f"Could not write config.toml: {exc}"},
            status_code=500,
        )

    settings.login_totp_secret = ""
    auth_log(
        settings,
        "totp",
        "two-factor login disabled",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "message": "Two-factor login is off."})


# ── Alert test ──────────────────────────────────────────────────────────────

@router.post("/ajax/check/alerts/test")
def alert_test(request: Request):
    settings = request.app.state.settings
    if not _alerts_configured(settings):
        return JSONResponse(
            {
                "ok": False,
                "message": "No alert channel is configured — set alerts.ntfy_url "
                           "or alerts.telegram_token + alerts.telegram_chat in "
                           "config.toml",
            }
        )

    ok, message = send_test_alert(settings)
    return JSONResponse({"ok": ok, "message": message})
