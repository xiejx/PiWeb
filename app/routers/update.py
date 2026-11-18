from __future__ import annotations

import hmac
import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..config import BASE_DIR
from ..executor import get_executor
from ..security import auth_configured, auth_log, client_ip, session_authenticated
from ..templating import render

router = APIRouter()

TAG_RE = re.compile(r"^v?[0-9]+\.[0-9]+(\.[0-9]+)?$")


def valid_key(settings, key: str) -> bool:
    if not settings.update_key or not key:
        return False
    return hmac.compare_digest(
        settings.update_key.encode("utf-8"), key.encode("utf-8")
    )


def authorized(request: Request, settings, key: str) -> bool:
    if valid_key(settings, key):
        return True
    if not auth_configured(settings):
        return True
    if session_authenticated(request, settings):
        return True
    if settings.basic_auth:
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            import base64

            try:
                user, password = base64.b64decode(header[6:]).decode("utf-8").split(":", 1)
            except (ValueError, UnicodeDecodeError):
                return False
            from ..security import verify_password

            if hmac.compare_digest(user, settings.basic_auth_user) and verify_password(
                settings.basic_auth_pass, password
            ):
                return True
    return False


def git_tags(executor) -> list[str]:
    result = executor.run(
        f"git -C {BASE_DIR} tag --sort=-v:refname 2>/dev/null", timeout=15
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def page(request: Request, settings, *, key: str = "", did_run: bool = False,
         ok: bool | None = None, output: str = "", selected: str = "master"):
    executor = get_executor(settings)
    return render(
        request,
        "update.html",
        tags=git_tags(executor),
        base_dir=str(BASE_DIR),
        key=key,
        did_run=did_run,
        ok=ok,
        output=output,
        selected=selected,
    )


@router.get("/update", response_class=HTMLResponse)
def update_page(request: Request, key: str = ""):
    settings = request.app.state.settings
    if not authorized(request, settings, key):
        return RedirectResponse("/login", status_code=303)
    return page(request, settings, key=key if valid_key(settings, key) else "")


@router.post("/update/run", response_class=HTMLResponse)
def update_run(
    request: Request,
    git_target: str = Form("master"),
    key: str = Form(""),
):
    settings = request.app.state.settings
    key = key or request.query_params.get("key", "")
    if not authorized(request, settings, key):
        return RedirectResponse("/login", status_code=303)

    target = git_target.strip() or "master"
    if target == "master":
        command = "git pull origin master 2>&1"
    elif TAG_RE.match(target):
        command = (
            f"git fetch --tags origin 2>&1"
            f" && git reset --hard refs/tags/{target} 2>&1"
        )
    else:
        return page(
            request,
            settings,
            key=key if valid_key(settings, key) else "",
            did_run=True,
            ok=False,
            output="Invalid target.",
            selected=target,
        )

    result = get_executor(settings).run(command, timeout=180, cwd=str(BASE_DIR))
    output = result.output
    if not output:
        output = "(no output — git may not be installed or the directory is not a repo)"
        ok = False
    else:
        lowered = output.lower()
        ok = result.ok and not any(
            marker in lowered for marker in ("error:", "fatal:", "permission denied")
        )

    auth_log(
        settings,
        "update",
        f"{target}: {'ok' if ok else 'failed'}: {output[:160]}",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return page(
        request,
        settings,
        key=key if valid_key(settings, key) else "",
        did_run=True,
        ok=ok,
        output=output[:20000],
        selected=target,
    )
