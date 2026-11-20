from __future__ import annotations

import re
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import button_store
from ..executor import get_executor
from ..security import (
    clear_login_failures,
    client_ip,
    ip_allowed,
    login_locked_for,
    record_login_failure,
)

router = APIRouter()

HASH_RE = re.compile(r"^[a-f0-9]{32}$")


async def _request_hash(request: Request, path_hash: str = "") -> str:
    supplied = request.headers.get("x-piweb-key", "").strip()
    if supplied:
        return supplied
    if path_hash:
        return path_hash.strip()
    query_hash = request.query_params.get("hash", "").strip()
    if query_hash:
        return query_hash
    if request.method == "POST":
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 - malformed body
            return ""
        return str(form.get("hash", "")).strip()
    return ""


def _json(payload: dict, status: int = 200, headers: dict | None = None) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=headers)


async def _handle(request: Request, path_hash: str = "") -> JSONResponse:
    settings = request.app.state.settings
    if not settings.module_enabled("button_api"):
        return _json({"success": False, "error": "Button API is disabled"}, 403)

    ip = client_ip(request, settings)
    user_agent = request.headers.get("user-agent", "")

    def log(hash_value: str, button: dict | None, status: str,
            output: str = "", error: str = "") -> None:
        button_store.api_log(
            settings,
            {
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ts": int(time.time()),
                "hash": (hash_value[:8] + "…") if hash_value else "",
                "button_title": (button or {}).get("button_title"),
                "command": (button or {}).get("button_command"),
                "status": status,
                "output": output or None,
                "error": error or None,
                "ip": ip or None,
                "user_agent": user_agent or None,
            },
        )

    if not ip_allowed(ip, settings.api_allow_ips):
        log("", None, "denied", error=f"IP not allowed: {ip}")
        return _json({"success": False, "error": "Forbidden"}, 403)

    throttle_key = f"api:{ip}"
    wait = login_locked_for(settings, throttle_key)
    if wait:
        log("", None, "locked", error=f"too many unknown keys from {ip}")
        return _json(
            {"success": False, "error": "Too many failed attempts"},
            429,
            {"Retry-After": str(wait)},
        )

    supplied = await _request_hash(request, path_hash)
    if not supplied or not HASH_RE.match(supplied):
        return _json({"success": False, "error": "Missing or invalid hash"}, 400)

    if button_store.load(settings) is None:
        return _json({"success": False, "error": "Failed to load buttons"}, 500)

    button = button_store.find_by_hash(settings, supplied)
    if button is None:
        record_login_failure(settings, throttle_key)
        log(supplied, None, "not_found")
        return _json({"success": False, "error": "Button not found"}, 404)

    clear_login_failures(settings, throttle_key)
    title = str(button.get("button_title", ""))
    command = str(button.get("button_command", "")).strip()
    if not command:
        log(supplied, button, "no_command")
        return _json(
            {"success": False, "button": title, "error": "Button has no command configured"},
            422,
        )

    executor = get_executor(settings)
    result = button_store.execute(settings, executor, button)
    if result.get("ok"):
        output = str(result.get("output", ""))
        log(supplied, button, "success", output=output)
        return _json({"success": True, "button": title, "output": output})

    error = str(result.get("error", ""))
    log(supplied, button, "error", error=error)
    return _json({"success": False, "button": title, "error": error}, 500)


@router.api_route("/api/button", methods=["GET", "POST"], include_in_schema=False)
async def button_api_root(request: Request):
    return await _handle(request)


@router.api_route(
    "/api/button/{button_hash}", methods=["GET", "POST"], include_in_schema=False
)
async def button_api_hash(request: Request, button_hash: str):
    return await _handle(request, button_hash)
