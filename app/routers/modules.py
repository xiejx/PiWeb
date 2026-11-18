from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import module_state
from ..security import auth_log, client_ip
from ..templating import flash, module_href, render

router = APIRouter()


def module_rows(settings) -> list[dict[str, object]]:
    overrides = module_state.load_state(settings)
    rows: list[dict[str, object]] = []
    for module in settings.modules.values():
        rows.append(
            {
                "key": module.key,
                "title": module.title,
                "group": module.group,
                "enabled": module.enabled,
                "overridden": module.key in overrides,
                "url": module.url,
                "iframe": module.iframe,
                "no_nav": module.no_nav,
                "external": bool(module.url),
                "href": f"/modules/open/{module.key}" if module.url else module_href(module),
            }
        )
    rows.sort(key=lambda row: (str(row["group"]).lower(), str(row["key"])))
    return rows


@router.get("/modules", response_class=HTMLResponse)
def modules_page(request: Request):
    settings = request.app.state.settings
    return render(request, "modules.html", modules=module_rows(settings))


@router.post("/modules/toggle")
def modules_toggle(
    request: Request,
    key: str = Form(""),
    enabled: str = Form("0"),
):
    settings = request.app.state.settings
    module = settings.modules.get(key.strip())
    if module is None:
        flash(request, "Unknown module.", "danger")
        return RedirectResponse("/modules", status_code=303)

    value = enabled.strip().lower() in {"1", "true", "on", "yes"}
    module_state.set_enabled(settings, module.key, value)
    flash(
        request,
        f"{module.title} {'enabled' if value else 'disabled'}.",
        "success",
    )
    auth_log(
        settings,
        "module",
        f"{module.key}: {'on' if value else 'off'}",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return RedirectResponse("/modules", status_code=303)


@router.get("/modules/open/{key}")
def module_open(request: Request, key: str):
    settings = request.app.state.settings
    module = settings.modules.get(key)
    if module is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if not module.enabled:
        flash(request, f"{module.title} is disabled.", "warning")
        return RedirectResponse("/", status_code=303)
    if not module.url:
        return RedirectResponse(f"/{module.key}", status_code=303)
    return render(request, "module_frame.html", module=module)
