from __future__ import annotations

import re

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..system import list_processes
from ..templating import flash, render

router = APIRouter()

NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


@router.get("/processes", response_class=HTMLResponse)
def processes_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(request, "processes.html", processes=list_processes(executor))


@router.get("/partials/processes", response_class=HTMLResponse)
def processes_partial(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(request, "partials/processes_table.html", processes=list_processes(executor))


@router.post("/processes/kill")
def kill_process(
    request: Request,
    pid: str = Form(""),
    pname: str = Form(""),
):
    settings = request.app.state.settings
    executor = get_executor(settings)
    if pid:
        if not pid.isdigit() or int(pid) <= 0:
            flash(request, "Process ID must be a positive integer.", "danger")
            return RedirectResponse("/processes", status_code=303)
        command = f"sudo kill -9 {int(pid)}"
        label = f"PID {pid}"
    elif pname:
        if not NAME_RE.match(pname.strip()):
            flash(request, "Process name may only contain letters, digits, hyphens, underscores and dots.", "danger")
            return RedirectResponse("/processes", status_code=303)
        command = f"sudo killall {pname.strip()}"
        label = pname.strip()
    else:
        flash(request, "Provide a process ID or name.", "danger")
        return RedirectResponse("/processes", status_code=303)

    result = executor.run(command, timeout=30)
    if result.ok:
        flash(request, f"Killed {label}.", "success")
    else:
        flash(request, f"Could not kill {label}: {result.output or 'no output'}", "danger")
    auth_log(settings, "process_kill", label, client_ip(request, settings),
             request.headers.get("user-agent", ""))
    return RedirectResponse("/processes", status_code=303)
