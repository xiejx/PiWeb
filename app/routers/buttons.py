from __future__ import annotations

import shlex

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import button_store, script_store
from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()

API_BASE = "/api/button/"


def _view(button: dict, index: int) -> dict[str, object]:
    style = str(button.get("button_style", ""))
    size = str(button.get("button_size", ""))
    return {
        "id": index,
        "title": str(button.get("button_title", "")) or "Untitled",
        "command": str(button.get("button_command", "")),
        "icon": str(button.get("button_icon", "")),
        "style": style if style in button_store.STYLES else "btn-default",
        "size": size if size in button_store.SIZES else "btn-md",
        "direct": bool(button.get("button_direct")),
        "hash": str(button.get("button_hash", "")),
    }


def _log(request: Request, settings, detail: str) -> None:
    auth_log(
        settings,
        "button",
        detail,
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )


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
    return {key: value for key, value in form.items()}


def _field(payload: dict, *names: str) -> object:
    for name in names:
        if name in payload and payload[name] is not None:
            return payload[name]
    return ""


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "on", "yes"}


def _id(payload: dict) -> str:
    return str(_field(payload, "id", "button_id") or "").strip()


@router.get("/buttons", response_class=HTMLResponse)
def buttons_page(request: Request):
    settings = request.app.state.settings
    stored = button_store.load(settings)
    load_error = ""
    if stored is None:
        stored = []
        load_error = "Warning: buttons.json is corrupted"

    scripts = []
    for row in script_store.list_scripts(settings):
        name = str(row.get("name", ""))
        if not name.endswith((".sh", ".py")):
            continue
        interpreter = "python3" if name.endswith(".py") else "bash"
        scripts.append(
            {
                "name": name,
                "run_line": f"{interpreter} {shlex.quote(str(settings.scripts_dir / name))}",
            }
        )

    return render(
        request,
        "buttons.html",
        buttons=[_view(button, index) for index, button in enumerate(stored)],
        scripts=scripts,
        load_error=load_error,
        button_api=settings.module_enabled("button_api"),
        api_base=API_BASE,
    )


@router.post("/ajax/buttons/submit")
async def buttons_submit(request: Request):
    settings = request.app.state.settings
    payload = await _payload(request)
    post = {
        "button_id": str(_field(payload, "button_id", "id") or "").strip(),
        "button_title": str(_field(payload, "title", "button_title") or ""),
        "button_command": str(_field(payload, "command", "button_command") or ""),
        "button_icon": str(_field(payload, "icon", "button_icon") or ""),
        "button_style": str(_field(payload, "style", "button_style") or ""),
        "button_size": str(_field(payload, "size", "button_size") or ""),
        "button_direct": _flag(_field(payload, "direct", "button_direct")),
    }
    ok, message, button = button_store.submit(settings, post)
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    _log(request, settings, f"submit: {post['button_title'][:80]}")
    return JSONResponse({"ok": True, "message": message, "button": button or {}})


@router.post("/ajax/buttons/delete")
async def buttons_delete(request: Request):
    settings = request.app.state.settings
    button_id = _id(await _payload(request))
    ok, message = button_store.delete(settings, button_id)
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    _log(request, settings, f"delete: {button_id}")
    return JSONResponse({"ok": True, "message": message})


@router.post("/ajax/buttons/execute")
async def buttons_execute(request: Request):
    settings = request.app.state.settings
    button_id = _id(await _payload(request))
    buttons = button_store.load(settings)
    if buttons is None:
        return JSONResponse({"ok": False, "error": "Could not read buttons file"})
    if not button_id.isdigit() or int(button_id) >= len(buttons):
        return JSONResponse({"ok": False, "error": "Button not found"})

    button = buttons[int(button_id)]
    result = button_store.execute(settings, get_executor(settings), button)
    _log(
        request,
        settings,
        f"execute {button_id}: {str(button.get('button_title', ''))[:80]}",
    )
    return JSONResponse(result)


@router.post("/ajax/buttons/reorder")
async def buttons_reorder(request: Request):
    settings = request.app.state.settings
    order = (await _payload(request)).get("order")
    if not isinstance(order, list):
        return JSONResponse({"ok": False, "error": "Invalid order"}, status_code=400)
    ok, message = button_store.reorder(settings, [str(item) for item in order])
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    return JSONResponse({"ok": True, "message": message})


@router.post("/ajax/buttons/regenerate")
async def buttons_regenerate(request: Request):
    settings = request.app.state.settings
    button_id = _id(await _payload(request))
    ok, message, new_hash = button_store.regenerate_hash(settings, button_id)
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    return JSONResponse(
        {"ok": True, "message": message, "button_hash": new_hash}
    )
