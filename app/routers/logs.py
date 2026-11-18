from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..executor import get_executor
from ..templating import render

router = APIRouter()

LOG_SOURCES: dict[str, str] = {
    "journal": "systemd journal (journalctl)",
    "dmesg": "Kernel ring buffer (dmesg)",
    "/var/log/syslog": "/var/log/syslog",
    "/var/log/messages": "/var/log/messages",
    "/var/log/auth.log": "/var/log/auth.log",
    "/var/log/kern.log": "/var/log/kern.log",
    "/var/log/daemon.log": "/var/log/daemon.log",
    "/var/log/nginx/error.log": "/var/log/nginx/error.log",
    "/var/log/nginx/access.log": "/var/log/nginx/access.log",
    "/var/log/piweb.log": "/var/log/piweb.log",
}
ALLOWED_LINES = {100, 200, 500, 1000, 2000}


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request):
    return render(request, "logs.html", sources=LOG_SOURCES)


def read_log(executor, source: str, lines: int) -> str:
    if source == "journal":
        result = executor.run(f"journalctl -n {lines} --no-pager 2>&1", timeout=30)
    elif source == "dmesg":
        result = executor.run(f"dmesg 2>/dev/null | tail -n {lines}", timeout=30)
    else:
        result = executor.run(f"tail -n {lines} {source} 2>&1", timeout=30)
    return result.stdout if result.stdout else result.stderr


@router.get("/logs/view", response_class=HTMLResponse)
def logs_view(request: Request, source: str = "journal", lines: int = 200, filter: str = ""):
    settings = request.app.state.settings
    executor = get_executor(settings)
    if source not in LOG_SOURCES:
        source = "journal"
    if lines not in ALLOWED_LINES:
        lines = 200

    output = read_log(executor, source, lines)
    if filter.strip():
        needle = filter.strip().lower()
        output = "\n".join(
            line for line in output.splitlines() if needle in line.lower()
        )
    if not output.strip():
        output = "(no matching lines)"
    return render(
        request,
        "partials/log_output.html",
        source=source,
        output=output[:400_000],
    )
