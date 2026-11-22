from __future__ import annotations

from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import link_store
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()


def _view(link: dict, index: int) -> dict[str, object]:
    url = str(link.get("url", ""))
    try:
        domain = urlparse(url).netloc
    except ValueError:
        domain = ""
    try:
        hits = max(0, int(link.get("hits", 0)))
    except (TypeError, ValueError):
        hits = 0
    return {
        "id": index,
        "title": str(link.get("title", "")) or "Untitled",
        "url": url,
        "domain": domain,
        "group": str(link.get("group", "")).strip(),
        "icon": str(link.get("icon", "")).strip()[:8],
        "hits": hits,
    }


def _grouped(views: list[dict]) -> list[dict[str, object]]:
    """Group links in first-seen order so the layout is deterministic."""
    groups: dict[str, list[dict]] = {}
    for view in views:
        groups.setdefault(str(view["group"]), []).append(view)
    return [{"name": name, "links": rows} for name, rows in groups.items()]


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


def _log(request: Request, settings, detail: str) -> None:
    auth_log(
        settings,
        "link",
        detail,
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )


@router.get("/links", response_class=HTMLResponse)
def links_page(request: Request):
    settings = request.app.state.settings
    stored = link_store.load(settings)
    load_error = ""
    if stored is None:
        stored = []
        load_error = "Warning: links.json is corrupted"
    views = [_view(link, index) for index, link in enumerate(stored)]
    return render(
        request,
        "links.html",
        links=views,
        groups=_grouped(views),
        load_error=load_error,
    )


@router.post("/ajax/links/submit")
async def links_submit(request: Request):
    settings = request.app.state.settings
    payload = await _payload(request)
    post = {
        "link_id": str(payload.get("link_id", "") or "").strip(),
        "title": str(payload.get("title", "") or ""),
        "url": str(payload.get("url", "") or ""),
        "group": str(payload.get("group", "") or ""),
        "icon": str(payload.get("icon", "") or ""),
    }
    ok, message, link = link_store.submit(settings, post)
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    _log(request, settings, f"submit: {post['title'][:80]}")
    return JSONResponse({"ok": True, "message": message, "link": link or {}})


@router.post("/ajax/links/delete")
async def links_delete(request: Request):
    settings = request.app.state.settings
    payload = await _payload(request)
    raw_id = str(payload.get("id", "") or "").strip()
    ok, message = link_store.delete(settings, raw_id)
    if not ok:
        return JSONResponse({"ok": False, "error": message}, status_code=400)
    _log(request, settings, f"delete: {raw_id}")
    return JSONResponse({"ok": True, "message": message})
