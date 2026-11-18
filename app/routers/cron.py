from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..cron_store import (
    add_job,
    delete_job,
    describe,
    read_system_crontab,
    read_user_crontab,
)
from ..executor import get_executor
from ..script_store import list_scripts
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()


def crontab_lines(executor) -> list[dict[str, object]]:
    text = read_user_crontab(executor)
    return [
        {"index": index, "line": line, "comment": line.strip().startswith("#")}
        for index, line in enumerate(text.splitlines())
        if line.strip()
    ]


@router.get("/cron", response_class=HTMLResponse)
def cron_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(
        request,
        "cron.html",
        jobs=crontab_lines(executor),
        system_crontab=read_system_crontab(),
        scripts=[row["name"] for row in list_scripts(settings)],
    )


@router.get("/partials/cron", response_class=HTMLResponse)
def cron_partial(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(request, "partials/cron_table.html", jobs=crontab_lines(executor))


async def _payload(request: Request) -> dict:
    """Accept both the JSON used by the page and plain form posts."""
    if "json" in request.headers.get("content-type", ""):
        try:
            data = await request.json()
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - malformed body
            return {}
    try:
        return dict(await request.form())
    except Exception:  # noqa: BLE001 - malformed body
        return {}


@router.post("/cron/add")
async def cron_add(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    payload = await _payload(request)
    schedule = str(payload.get("schedule", ""))
    command = str(payload.get("command", ""))
    if not schedule or not command:
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)

    ok, message = add_job(executor, schedule, command)
    if ok:
        auth_log(settings, "cron_add", f"{schedule} {command[:160]}",
                 client_ip(request, settings), request.headers.get("user-agent", ""))
    return JSONResponse(
        {"ok": ok, "message": message, "description": describe(schedule)},
        status_code=200 if ok else 400,
    )


@router.post("/cron/delete")
async def cron_delete(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    payload = await _payload(request)
    try:
        index = int(payload.get("index"))
    except (TypeError, ValueError):
        return JSONResponse({"ok": False, "error": "Invalid index"}, status_code=400)
    ok, message = delete_job(executor, index)
    if ok:
        auth_log(settings, "cron_delete", f"index {index}", client_ip(request, settings),
                 request.headers.get("user-agent", ""))
    return JSONResponse({"ok": ok, "message": message}, status_code=200 if ok else 400)

