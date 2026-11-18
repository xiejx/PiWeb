from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import script_store
from ..cron_store import schedule_script, scripts_with_schedules, unschedule_script
from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()


@router.get("/scripts", response_class=HTMLResponse)
def scripts_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    names = [row["name"] for row in script_store.list_scripts(settings)]
    return render(
        request,
        "scripts.html",
        schedules=scripts_with_schedules(executor, names),
    )


@router.get("/scripts/list")
def scripts_list(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    scripts = script_store.list_scripts(settings)
    names = [row["name"] for row in scripts]
    return JSONResponse(
        {
            "ok": True,
            "scripts": scripts,
            "examples": script_store.list_examples(),
            "pins": script_store.pins(settings),
            "schedules": scripts_with_schedules(executor, names),
        }
    )


@router.get("/scripts/content")
def scripts_content(request: Request, name: str = "", template: int = 0):
    settings = request.app.state.settings
    content = script_store.load(settings, name, from_template=bool(template))
    if content is None:
        return JSONResponse({"ok": False, "error": "Script not found"}, status_code=404)
    return JSONResponse({"ok": True, "name": name, "content": content})


@router.post("/scripts/save")
async def scripts_save(request: Request):
    settings = request.app.state.settings
    try:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        content = str(payload.get("content", ""))
    except (ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)
    error = script_store.save(settings, name, content)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    return JSONResponse({"ok": True, "name": name})


@router.post("/scripts/delete")
async def scripts_delete(request: Request):
    settings = request.app.state.settings
    try:
        payload = await request.json()
        name = str(payload.get("name", ""))
    except (ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)
    error = script_store.delete(settings, name)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    auth_log(settings, "script_delete", name, client_ip(request, settings),
             request.headers.get("user-agent", ""))
    return JSONResponse({"ok": True})


@router.post("/scripts/run")
async def scripts_run(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    try:
        payload = await request.json()
        name = str(payload.get("name", "")).strip()
        content = payload.get("content")
        background = bool(payload.get("background"))
    except (ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)

    if isinstance(content, str):
        error = script_store.save(settings, name, content)
        if error:
            return JSONResponse({"ok": False, "error": error}, status_code=400)

    result = script_store.run(settings, executor, name, background=background)
    auth_log(
        settings,
        "script_run",
        f"{name}{' (background)' if background else ''}",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    return JSONResponse(result)


@router.post("/scripts/pin")
async def scripts_pin(request: Request):
    settings = request.app.state.settings
    try:
        payload = await request.json()
        name = str(payload.get("name", ""))
        pinned = bool(payload.get("pinned"))
    except (ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)
    error = script_store.set_pin(settings, name, pinned)
    if error:
        return JSONResponse({"ok": False, "error": error}, status_code=400)
    return JSONResponse({"ok": True, "pins": script_store.pins(settings)})


@router.post("/scripts/schedule")
async def scripts_schedule(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    try:
        payload = await request.json()
        name = str(payload.get("name", ""))
        schedule = str(payload.get("schedule", "")).strip()
    except (ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)

    if script_store.resolve(settings, name, must_exist=True) is None:
        return JSONResponse({"ok": False, "error": "Save the script first"}, status_code=400)

    if not schedule:
        ok, message = unschedule_script(executor, name)
    else:
        interpreter = "python3" if name.endswith(".py") else "bash"
        ok, message = schedule_script(executor, name, schedule, interpreter)
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 400)
