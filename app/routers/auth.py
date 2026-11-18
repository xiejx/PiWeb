from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from ..config import save_auth_credentials
from ..executor import Executor
from ..security import (
    auth_configured,
    auth_log,
    clear_login_failures,
    client_ip,
    end_session,
    hash_password,
    is_local_ip,
    login_locked_for,
    mark_login,
    record_login_failure,
    totp_verify_login,
    verify_password,
)
from ..templating import flash, render

router = APIRouter()


@router.get("/login")
def login_page(request: Request, action: str = "", wait: int = 0):
    settings = request.app.state.settings
    if not auth_configured(settings) and not settings.allow_unauthenticated:
        return RedirectResponse("/setup", status_code=303)
    if request.session.get("user_hash") or request.session.get("sys_user"):
        return RedirectResponse("/", status_code=303)
    just_expired = bool(request.session.pop("just_expired", False))
    return render(
        request,
        "login.html",
        action=action,
        wait=wait,
        just_expired=just_expired,
        totp_enabled=bool(settings.login_totp_secret),
    )


@router.post("/login")
def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    totp: str = Form(""),
):
    settings = request.app.state.settings
    ip = client_ip(request, settings)
    agent = request.headers.get("user-agent", "")

    wait = login_locked_for(settings, ip)
    if wait:
        auth_log(settings, "locked", "attempt while locked out", ip, agent)
        return RedirectResponse(f"/login?action=locked&wait={wait}", status_code=303)

    mode = ""
    if settings.login_check_system_user:
        if _system_login(settings, username.strip(), password):
            mode = "system"
    elif (
        username.strip() == settings.login_user
        and verify_password(settings.login_pass, password)
    ):
        mode = "config"

    if mode and not totp_verify_login(settings, totp):
        mode = ""
        reason = f"user: {username} (wrong or reused 2FA code)"
    else:
        reason = f"user: {username}"

    if mode:
        clear_login_failures(settings, ip)
        mark_login(request, username.strip(), mode)
        request.session.setdefault("csrf", "")
        auth_log(settings, "login_ok", f"{mode} account: {username}", ip, agent)
        return RedirectResponse("/", status_code=303)

    record_login_failure(settings, ip)
    auth_log(settings, "login_failed", reason, ip, agent)
    if login_locked_for(settings, ip):
        try:
            from ..alerts import alert_once

            alert_once(
                settings,
                f"lockout:{ip}",
                "Login lockout",
                f"Address {ip} was locked out after repeated failed logins.",
                900,
            )
        except Exception:  # noqa: BLE001 - alerts must never break login
            pass
    return RedirectResponse("/login?action=incorrect_login", status_code=303)


@router.get("/logout")
def logout(request: Request):
    settings = request.app.state.settings
    if request.session.get("user_hash") or request.session.get("sys_user"):
        auth_log(
            settings,
            "logout",
            "",
            client_ip(request, settings),
            request.headers.get("user-agent", ""),
        )
    end_session(request)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/setup")
def setup_page(request: Request):
    settings = request.app.state.settings
    configured = auth_configured(settings)
    ip = client_ip(request, settings)
    return render(
        request,
        "setup.html",
        configured=configured,
        allowed=is_local_ip(ip) or ip in {"", "testclient"},
        client_ip=ip,
        errors=[],
        done=False,
        username="",
    )


@router.post("/setup")
def setup_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    password2: str = Form(""),
):
    settings = request.app.state.settings
    ip = client_ip(request, settings)
    configured = auth_configured(settings)
    allowed = is_local_ip(ip) or ip in {"", "testclient"}

    errors: list[str] = []
    if configured:
        return render(request, "setup.html", configured=True, allowed=allowed,
                      client_ip=ip, errors=[], done=False, username="")
    if not allowed:
        return render(request, "setup.html", configured=False, allowed=False,
                      client_ip=ip, errors=[], done=False, username="")

    username = username.strip()
    import re

    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,50}", username):
        errors.append("Username must be 1–50 characters (letters, digits, and . _ @ - only).")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password.lower() in {"raspberry", "password", "secret", "piweb", "admin"}:
        errors.append("That password is one of the well-known defaults — choose another.")
    if password != password2:
        errors.append("The two passwords do not match.")

    if errors:
        return render(request, "setup.html", configured=False, allowed=True,
                      client_ip=ip, errors=errors, done=False, username=username)

    config_path = settings.config_path
    if config_path is None:
        config_path = settings.data_dir.parent / "config.toml"
    try:
        save_auth_credentials(config_path, username, hash_password(password))
    except OSError as exc:
        errors.append(f"Could not write {config_path}: {exc}")
        return render(request, "setup.html", configured=False, allowed=True,
                      client_ip=ip, errors=errors, done=False, username=username)

    settings.login_required = True
    settings.login_user = username
    settings.login_pass = hash_password(password)
    settings.allow_unauthenticated = False
    auth_log(settings, "login_ok", f"setup completed for {username}", ip,
             request.headers.get("user-agent", ""))
    return render(request, "setup.html", configured=True, allowed=True,
                  client_ip=ip, errors=[], done=True, username=username)


def _system_login(settings, username: str, password: str) -> bool:
    if not username or not password:
        return False
    if username != settings.login_user:
        return False
    previous_mode = settings.execution_mode
    try:
        settings.execution_mode = "ssh"
        result = Executor(settings).run("true", timeout=8)
        return result.ok
    finally:
        settings.execution_mode = previous_mode
