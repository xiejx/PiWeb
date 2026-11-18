from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..alerts import evaluate
from ..dashboard import collect, summary
from ..executor import get_executor
from ..script_store import tile_output
from ..templating import render
from ..templating import build_nav

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    inline, groups = build_nav(settings)
    data = collect(settings, executor)
    evaluate(settings, data)
    return render(
        request,
        "dashboard.html",
        d=data,
        nav_modules=inline,
    )


@router.get("/partials/dashboard/live", response_class=HTMLResponse)
def dashboard_live(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = collect(settings, executor)
    evaluate(settings, data)
    return render(request, "partials/dashboard_live.html", d=data)


@router.get("/ajax/dashboard/events")
async def dashboard_events(request: Request):
    """Server-sent events with a compact summary every two seconds."""
    settings = request.app.state.settings
    executor = get_executor(settings)

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await run_in_threadpool(summary, settings, executor)
                except Exception as exc:  # noqa: BLE001 - keep the stream alive
                    payload = {"error": str(exc)}
                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/partials/dashboard/tile", response_class=HTMLResponse)
def dashboard_tile(request: Request, name: str = "", force: int = 0):
    settings = request.app.state.settings
    executor = get_executor(settings)
    tile = tile_output(settings, executor, name, force=bool(force))
    return render(request, "partials/script_tile.html", name=name, tile=tile)


@router.post("/ajax/menu_order")
async def save_menu_order(request: Request):
    settings = request.app.state.settings
    try:
        payload = await request.json()
        order = payload.get("order", [])
    except (ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid JSON"}, status_code=400)

    inline, _ = build_nav(settings)
    known = {module.key for module in inline}
    cleaned = [str(key) for key in order if str(key) in known]
    if not cleaned:
        return JSONResponse({"ok": False, "error": "Empty menu order"}, status_code=400)

    path = settings.data_dir / "menu_order.json"
    path.write_text(json.dumps(cleaned), encoding="utf-8")
    return JSONResponse({"ok": True, "order": cleaned})
