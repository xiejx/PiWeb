from __future__ import annotations

import re
import shlex
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
        f"git -C {shlex.quote(str(BASE_DIR))} tag --sort=-v:refname 2>/dev/null",
        timeout=15,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def git_run(executor, args: str, timeout: float = 15):
    """Run git against the deployed tree — deliberately without sudo.

    The installer chowns the tree to the service user, so git does not need
    root; running it as root would leave root-owned objects behind and break
    the next update. sudo would also fail outright here: the service has no
    terminal to answer a password prompt, which is what surfaces as
    "sudo: a terminal is required to read the password".
    """
    return executor.run(f"git -C {shlex.quote(str(BASE_DIR))} {args} 2>&1", timeout=timeout)


def git_repo_state(executor) -> dict[str, object]:
    """Whether the deployed tree is a git checkout, and where it pulls from.

    installer.sh rsyncs a local checkout when it is run from one, and only
    clones when the target has no .git, so a normal install is often not a
    repository at all — in that case the panel cannot update itself.
    """
    inside = git_run(executor, "rev-parse --is-inside-work-tree", timeout=10)
    lines = [line.strip() for line in inside.stdout.splitlines() if line.strip()]
    repo = bool(inside.ok and lines and lines[-1] == "true")
    remote = ""
    if repo:
        result = git_run(executor, "remote get-url origin", timeout=10)
        if result.ok and result.stdout.strip():
            remote = result.stdout.strip().splitlines()[-1]
    return {"repo": repo, "remote": remote}


def git_update_command(target: str, state: dict[str, object]) -> tuple[str, str]:
    """Build the update command for `target`, or explain why there is none.

    Returns (command, error); the caller puts either into the result card.
    """
    if not state.get("repo"):
        return "", (
            "This installation is not a git checkout, so it cannot update itself. "
            "It was installed from a local copy — re-run installer.sh from a newer "
            "checkout to upgrade."
        )
    if not state.get("remote"):
        return "", "This checkout has no 'origin' remote, so there is nothing to pull from."
    repo = shlex.quote(str(BASE_DIR))
    if target == "master":
        return f"git -C {repo} pull --ff-only origin master 2>&1", ""
    if TAG_RE.match(target):
        return (
            f"git -C {repo} fetch --tags --force origin 2>&1"
            f" && git -C {repo} reset --hard refs/tags/{target} 2>&1"
        ), ""
    return "", "Invalid release tag."


def classify_git_error(output: str) -> str:
    """Map git's wording onto a reason the UI can translate."""
    text = (output or "").lower()
    if "command not found" in text or "git: not found" in text:
        return "git_missing"
    if "not a git repository" in text:
        return "not_a_repo"
    if "does not appear to be a git repository" in text or "no such remote" in text:
        return "no_remote"
    if ("permission denied" in text or "unable to create" in text
            or "read-only file system" in text or "could not open" in text):
        return "permission"
    if ("could not resolve host" in text or "network is unreachable" in text
            or "connection timed out" in text or "temporary failure in name resolution" in text
            or "connection refused" in text):
        return "network"
    if ("authentication failed" in text or "could not read username" in text
            or "permission to" in text or "access denied" in text):
        return "auth"
    return "failed"


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
        command, error = git_update_command(
            git_target.strip() or "master", git_repo_state(executor)
        )
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
        # Detach the power-off so this response can reach the browser first,
        # but keep its output in a log instead of /dev/null: a failed sudo used
        # to be invisible while the page still claimed the shutdown was set.
        log_name = "poweroff.log"
        log_path = settings.command_logs_dir / log_name
        result = executor.run_background("sleep 2; sudo poweroff", log_path)
        if not result.ok:
            request.session["action_result"] = {
                "level": "danger",
                "message": "Could not schedule shutdown.",
                "command": "sudo poweroff",
                "output": result.output[:20000],
            }
        else:
            request.session["action_result"] = {
                "level": "warning",
                "message": "Shutdown scheduled — the Pi will power off in 2 seconds.",
                "command": "sudo poweroff",
                "output": (
                    "If the Pi stays online, check command_logs/poweroff.log "
                    "for the poweroff error."
                ),
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
    tags = git_tags(executor)
    state = git_repo_state(executor)

    # Answer "why" instead of pasting git's output: a copy-install and a
    # password-prompting sudo look the same to the operator otherwise.
    if not state["repo"]:
        return JSONResponse({"ok": False, "reason": "not_a_repo", "tags": tags, "error": ""})
    if not state["remote"]:
        return JSONResponse({"ok": False, "reason": "no_remote", "tags": tags, "error": ""})

    result = git_run(executor, "fetch --tags --force origin", timeout=90)
    if result.ok:
        return JSONResponse({"ok": True, "tags": git_tags(executor), "error": ""})
    return JSONResponse(
        {
            "ok": False,
            "reason": classify_git_error(result.output),
            "tags": tags,
            "error": result.output[-400:],
        }
    )
