from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()

# `apt list --upgradable` line:
#   name/suite newver arch [upgradable from: oldver]
UPGRADABLE_RE = re.compile(
    r"^(\S+?)/\S+\s+(\S+)\s+\S+\s+\[upgradable from:\s+([^\]]+)\]"
)
# apt writes repository problems to stderr as E:/Err:/W:/N: lines.
REPO_PROBLEM_RE = re.compile(r"^(?:E:|Err:|W:|N: Updating from such)", re.MULTILINE)
INDEX_PATHS = ("/var/lib/apt/periodic/update-success-stamp", "/var/lib/apt/lists")


def index_mtime() -> int:
    """Newest mtime of the apt update stamp or the lists directory (0 if unknown)."""
    stamp = 0
    for raw in INDEX_PATHS:
        try:
            mtime = int(Path(raw).stat().st_mtime)
        except OSError:
            continue
        stamp = max(stamp, mtime)
    return stamp


def parse_upgradable(text: str) -> list[dict[str, str]]:
    packages: list[dict[str, str]] = []
    for line in text.splitlines():
        match = UPGRADABLE_RE.match(line.strip())
        if not match:
            continue
        packages.append(
            {
                "name": match.group(1),
                "new": match.group(2),
                "current": match.group(3).strip(),
            }
        )
    return packages


def package_state(executor) -> dict:
    result = executor.run("apt list --upgradable 2>/dev/null", timeout=60)
    packages = parse_upgradable(result.stdout)
    return {
        "packages": packages,
        "count": len(packages),
        "index_mtime": index_mtime(),
    }


def human_age(seconds: int) -> str:
    if seconds < 90:
        return "just now"
    minutes = round(seconds / 60)
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = round(minutes / 60)
    if hours < 36:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = round(hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


@router.get("/packages", response_class=HTMLResponse)
def packages_page(request: Request):
    settings = request.app.state.settings
    state = package_state(get_executor(settings))
    age = max(0, int(time.time()) - state["index_mtime"]) if state["index_mtime"] else 0
    return render(
        request,
        "packages.html",
        **state,
        index_age=human_age(age) if age else "",
        index_stale=bool(age and age > 86400),
    )


@router.get("/ajax/packages/list")
def packages_list(request: Request):
    settings = request.app.state.settings
    return JSONResponse({"ok": True, **package_state(get_executor(settings))})


@router.post("/ajax/packages/check")
def packages_check(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    result = executor.run("sudo apt-get update 2>&1", timeout=300)
    output = result.output
    auth_log(settings, "package_check", "", client_ip(request, settings),
             request.headers.get("user-agent", ""))
    if not result.ok:
        return JSONResponse(
            {"ok": False, "output": output,
             "message": output or "apt-get update failed"}
        )
    if REPO_PROBLEM_RE.search(output):
        return JSONResponse(
            {"ok": False, "output": output,
             "message": "apt reported repository problems — your sources may be "
                        "unreachable or end-of-life."}
        )
    return JSONResponse(
        {"ok": True, "output": output, "message": "Package index refreshed"}
    )


@router.post("/ajax/packages/upgrade")
def packages_upgrade(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    result = executor.run(
        "sudo DEBIAN_FRONTEND=noninteractive apt-get -y upgrade 2>&1", timeout=900
    )
    auth_log(settings, "package_upgrade", "", client_ip(request, settings),
             request.headers.get("user-agent", ""))
    if not result.ok:
        return JSONResponse(
            {"ok": False, "output": result.output,
             "message": result.output or "apt-get upgrade failed"}
        )
    return JSONResponse(
        {"ok": True, "output": result.output, "message": "Upgrade finished"}
    )
