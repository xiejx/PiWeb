from __future__ import annotations

import re
import time
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..config import BASE_DIR
from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import flash, render

router = APIRouter()

NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
TAG_RE = re.compile(r"^v?[0-9]+\.[0-9]+(\.[0-9]+)?$")
RESERVED_LOGS = {"auth.log", "api_calls.log", "module_install.log"}


def git_tags(executor) -> list[str]:
    result = executor.run(
        f"git -C {BASE_DIR} tag --sort=-v:refname 2>/dev/null", timeout=15
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def list_command_logs(settings) -> list[dict[str, object]]:
    directory = settings.command_logs_dir
    logs: list[dict[str, object]] = []
    if not directory.is_dir():
        return logs
    for path in directory.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name in RESERVED_LOGS:
            continue
        if not path.name.endswith((".log", ".txt")):
            continue
        stat = path.stat()
        logs.append({"name": path.name, "mtime": int(stat.st_mtime), "size": stat.st_size})
    logs.sort(key=lambda row: int(row["mtime"]), reverse=True)
    return logs


def resolve_log(settings, name: str) -> Path | None:
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    if not name.endswith((".log", ".txt")) or name in RESERVED_LOGS:
        return None
    path = settings.command_logs_dir / name
    if path.is_symlink() or not path.is_file():
        return None
    return path


@router.get("/actions", response_class=HTMLResponse)
def actions_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    result = request.session.pop("action_result", None)
    return render(
        request,
        "actions.html",
        result=result,
        logs=list_command_logs(settings),
        tags=git_tags(executor),
        button_api=settings.module_enabled("button_api"),
    )


@router.post("/actions/run")
def actions_run(
    request: Request,
    action: str = Form(""),
    pid: str = Form(""),
    pname: str = Form(""),
    sname: str = Form(""),
    cmd: str = Form(""),
    background_command: str = Form(""),
    git_target: str = Form("master"),
):
    settings = request.app.state.settings
    executor = get_executor(settings)
    command = ""
    error = ""

    if action == "kill_pid":
        if pid.isdigit() and int(pid) > 0:
            command = f"sudo kill -9 {int(pid)}"
        else:
            error = "Process ID must be a positive integer."
    elif action == "kill_pname":
        if NAME_RE.match(pname.strip()):
            command = f"sudo killall {pname.strip()}"
        else:
            error = "Process name may only contain letters, digits, hyphens, underscores and dots."
    elif action in {"start_sname", "stop_sname"}:
        verb = "start" if action.startswith("start") else "stop"
        if NAME_RE.match(sname.strip()):
            command = f"sudo service {sname.strip()} {verb}"
        else:
            error = "Service name may only contain letters, digits, hyphens, underscores and dots."
    elif action == "reboot":
        command = "sudo reboot"
    elif action == "shutdown":
        command = "sudo poweroff"
    elif action == "git_pull":
        target = git_target.strip() or "master"
        base = f"cd {BASE_DIR}"
        if target == "master":
            command = f"{base} && sudo git pull origin master 2>&1"
        elif TAG_RE.match(target):
            command = (
                f"{base} && sudo git fetch --tags origin 2>&1"
                f" && sudo git reset --hard refs/tags/{target} 2>&1"
            )
        else:
            error = "Invalid release tag."
    elif action == "cmd":
        if cmd.strip():
            command = cmd.strip()
        else:
            error = "Command cannot be empty."
    else:
        error = "Unknown action."

    if error:
        request.session["action_result"] = {
            "level": "danger",
            "message": error,
            "command": "",
            "output": "",
        }
        return RedirectResponse("/actions", status_code=303)

    if action == "shutdown":
        # Detach the power-off so this response can reach the browser first.
        executor.run(
            "nohup sh -c 'sleep 2; sudo poweroff' >/dev/null 2>&1 &", timeout=15
        )
        request.session["action_result"] = {
            "level": "warning",
            "message": "Shutdown scheduled — the Pi will power off in 2 seconds.",
            "command": "sudo poweroff",
            "output": "Shutting down. The panel will become unreachable shortly.",
        }
        auth_log(
            settings,
            "action",
            "shutdown",
            client_ip(request, settings),
            request.headers.get("user-agent", ""),
        )
        return RedirectResponse("/actions", status_code=303)

    background = bool(background_command)
    if background:
        log_name = time.strftime("%Y-%m-%d_%H-%M-%S") + "_background.log"
        log_path = settings.command_logs_dir / log_name
        result = executor.run_background(command, log_path)
        output = result.stderr if not result.ok else f"Running in background. Log: {log_name}"
        request.session["action_result"] = {
            "level": "success" if result.ok else "danger",
            "message": "Command started in the background." if result.ok else "Could not start the command.",
            "command": command,
            "output": output,
        }
    else:
        result = executor.run(command, timeout=120)
        output = result.output
        if not result.stdout and not result.stderr:
            output = "(no output)"
        request.session["action_result"] = {
            "level": "success" if result.ok else "danger",
            "message": "Command executed successfully." if result.ok else "Command failed.",
            "command": command,
            "output": output[:20000],
        }

    auth_log(
        settings,
        "action",
        f"{action}: {command[:160]}",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return RedirectResponse("/actions", status_code=303)


@router.get("/actions/log", response_class=HTMLResponse)
def view_log(request: Request, file: str = ""):
    settings = request.app.state.settings
    path = resolve_log(settings, file)
    if path is None:
        return render(request, "partials/log_viewer.html", file=file, content="", error="Log not found.")
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > 200_000:
        content = content[-200_000:]
        content = "… (truncated to the last 200 KB)\n" + content
    return render(request, "partials/log_viewer.html", file=file, content=content, error="")


@router.post("/actions/log/delete")
def delete_log(request: Request, file: str = Form("")):
    settings = request.app.state.settings
    path = resolve_log(settings, file)
    if path is None:
        return JSONResponse({"ok": False, "error": "Log not found"}, status_code=404)
    try:
        path.unlink()
    except OSError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True})


@router.post("/ajax/git_tags")
def refresh_git_tags(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    result = executor.run(f"cd {BASE_DIR} && sudo git fetch --tags origin 2>&1", timeout=90)
    return JSONResponse(
        {
            "ok": True,
            "tags": git_tags(executor),
            "error": "" if result.ok else result.output[-500:],
        }
    )
