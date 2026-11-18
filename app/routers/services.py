from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import flash, render

router = APIRouter()

NAME_RE = re.compile(r"^[a-zA-Z0-9_.@:-]+$")


def service_lists(executor) -> dict[str, list[str]]:
    result = executor.run("service --status-all 2>&1", timeout=20)
    active: list[str] = []
    inactive: list[str] = []
    unknown: list[str] = []
    for line in result.stdout.splitlines():
        match = re.match(r"\s*\[\s*([+\-?])\s*\]\s*(\S+)", line)
        if not match:
            continue
        state, name = match.group(1), match.group(2)
        if state == "+":
            active.append(name)
        elif state == "-":
            inactive.append(name)
        else:
            unknown.append(name)
    if not active and not inactive and not unknown:
        # `service --status-all` is unavailable (some minimal images): fall back
        # to systemd's unit list so the page still has content.
        from ..system import list_services

        for unit in list_services(executor):
            name = unit["name"]
            if unit["active"] == "active":
                active.append(name)
            elif unit["active"] == "inactive":
                inactive.append(name)
            else:
                unknown.append(name)
    return {"active": sorted(active), "inactive": sorted(inactive), "unknown": sorted(unknown)}


@router.get("/services", response_class=HTMLResponse)
def services_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(request, "services.html", lists=service_lists(executor))


@router.get("/partials/services", response_class=HTMLResponse)
def services_partial(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(request, "partials/services_table.html", lists=service_lists(executor))


@router.post("/services/action")
def service_action(request: Request, sname: str = Form(""), action: str = Form("")):
    settings = request.app.state.settings
    executor = get_executor(settings)
    sname = sname.strip()
    if not NAME_RE.match(sname) or action not in {"start", "stop", "restart"}:
        flash(request, "Invalid service or action.", "danger")
        return RedirectResponse("/services", status_code=303)
    result = executor.run(f"sudo service {sname} {action}", timeout=60)
    if result.ok:
        flash(request, f"Service {sname}: {action} succeeded.", "success")
    else:
        flash(request, f"Service {sname}: {action} failed — {result.output or 'no output'}", "danger")
    auth_log(settings, "service", f"{action} {sname}", client_ip(request, settings),
             request.headers.get("user-agent", ""))
    return RedirectResponse("/services", status_code=303)
