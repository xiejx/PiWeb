from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import wifi
from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()


async def _payload(request: Request) -> dict:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}
    try:
        form = await request.form()
    except Exception:  # noqa: BLE001 - malformed body
        return {}
    return dict(form)


@router.get("/wifi", response_class=HTMLResponse)
def wifi_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(request, "wifi.html", state=wifi.status(executor))


@router.get("/ajax/wifi/status")
def wifi_status(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse({"ok": True, "state": wifi.status(executor)})


@router.post("/ajax/wifi/scan")
def wifi_scan(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    try:
        networks = wifi.scan(executor)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    auth_log(
        settings,
        "wifi_scan",
        f"{len(networks)} networks",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "networks": networks})


@router.post("/ajax/wifi/connect")
async def wifi_connect(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)
    ssid = str(data.get("ssid", ""))
    password = str(data.get("password", ""))
    try:
        message = wifi.connect(executor, ssid, password)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        auth_log(
            settings,
            "wifi_connect",
            f"{ssid}: failed",
            client_ip(request, settings),
            request.headers.get("user-agent", ""),
        )
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    auth_log(
        settings,
        "wifi_connect",
        ssid,
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "message": message, "state": wifi.status(executor)})


@router.post("/ajax/wifi/disconnect")
def wifi_disconnect(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    try:
        message = wifi.disconnect(executor)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    auth_log(
        settings,
        "wifi_disconnect",
        "ok",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "message": message, "state": wifi.status(executor)})


@router.post("/ajax/wifi/radio")
async def wifi_radio(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)
    try:
        enabled = bool(int(data.get("enable", 0) or 0))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Invalid radio state"}, status_code=400)
    try:
        message = wifi.set_radio(executor, enabled)
    except (RuntimeError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    auth_log(
        settings,
        "wifi_radio",
        "on" if enabled else "off",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "message": message, "state": wifi.status(executor)})
