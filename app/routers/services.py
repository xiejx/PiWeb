from __future__ import annotations

import re
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..system import list_services, list_unit_files
from ..templating import flash, render

router = APIRouter()

NAME_RE = re.compile(r"^[a-zA-Z0-9_.@:-]+$")

# systemctl is the only interface — PiWeb no longer speaks SysV.
ACTIONS = {"start", "stop", "restart", "enable", "disable"}

# Colour per systemd ACTIVE state; anything unexpected falls back to secondary.
STATE_TONES = {
    "active": "success",
    "reloading": "info",
    "activating": "warning",
    "deactivating": "warning",
    "failed": "danger",
    "inactive": "secondary",
}

# Same order systemd prints them in: failures first, then transitions, then
# running units, then everything idle.
STATE_ORDER = {
    "failed": 0,
    "activating": 1,
    "deactivating": 1,
    "reloading": 2,
    "active": 3,
    "inactive": 4,
}

FILTERS = (
    {"key": "all", "i18n": "svc.filters.all", "label": "All", "tone": "secondary"},
    {"key": "active", "i18n": "svc.filters.active", "label": "Active", "tone": "success"},
    {"key": "failed", "i18n": "svc.filters.failed", "label": "Failed", "tone": "danger"},
    {"key": "inactive", "i18n": "svc.filters.inactive", "label": "Inactive", "tone": "secondary"},
)


def state_matches(row: dict[str, str], state: str) -> bool:
    active = row["active"]
    if state == "active":
        return active == "active"
    if state == "failed":
        return active == "failed"
    if state == "inactive":
        return active in {"inactive", "deactivating"}
    return True


def service_view(executor, q: str = "", state: str = "all") -> dict[str, object]:
    """Join runtime state (list-units) with boot behaviour (list-unit-files)."""
    files = list_unit_files(executor)

    rows: list[dict[str, str]] = []
    for unit in list_services(executor):
        meta = files.get(unit["unit"], {})
        rows.append(
            {
                **unit,
                "file_state": meta.get("state", ""),
                "tone": STATE_TONES.get(unit["active"], "secondary"),
            }
        )
    rows.sort(key=lambda row: (STATE_ORDER.get(row["active"], 5), row["name"]))

    counts = {
        flt["key"]: sum(1 for row in rows if state_matches(row, flt["key"])) for flt in FILTERS
    }

    # Space-separated terms are ANDed, so "docker container" narrows down.
    terms = q.lower().split()
    visible = [
        row
        for row in rows
        if state_matches(row, state)
        and all(
            term in row["name"].lower()
            or term in row["unit"].lower()
            or term in row["description"].lower()
            for term in terms
        )
    ]

    return {
        "rows": visible,
        "counts": counts,
        "filters": FILTERS,
        "state": state if state in counts else "all",
        "q": q.strip(),
        "total": len(rows),
    }


def back_url(q: str, state: str) -> str:
    params: dict[str, str] = {}
    if q.strip():
        params["q"] = q.strip()
    if state and state != "all":
        params["state"] = state
    return "/services" + (f"?{urlencode(params)}" if params else "")


@router.get("/services", response_class=HTMLResponse)
def services_page(request: Request, q: str = "", state: str = "all"):
    settings = request.app.state.settings
    return render(request, "services.html", **service_view(get_executor(settings), q, state))


@router.get("/partials/services", response_class=HTMLResponse)
def services_partial(request: Request, q: str = "", state: str = "all"):
    settings = request.app.state.settings
    return render(
        request,
        "partials/services_table.html",
        **service_view(get_executor(settings), q, state),
    )


@router.post("/services/action")
def service_action(
    request: Request,
    sname: str = Form(""),
    action: str = Form(""),
    q: str = Form(""),
    state: str = Form("all"),
):
    settings = request.app.state.settings
    executor = get_executor(settings)
    sname = sname.strip()

    if not NAME_RE.match(sname) or action not in ACTIONS:
        flash(request, "Invalid service or action.", "danger")
        return RedirectResponse(back_url(q, state), status_code=303)

    result = executor.run(f"sudo systemctl {action} {sname}", timeout=60)
    if result.ok:
        flash(request, f"{sname}: systemctl {action} succeeded.", "success")
    else:
        flash(
            request,
            f"{sname}: systemctl {action} failed — {result.output or 'no output'}",
            "danger",
        )
    auth_log(
        settings,
        "service",
        f"{action} {sname}",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return RedirectResponse(back_url(q, state), status_code=303)
