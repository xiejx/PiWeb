from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..docker import (
    COMPOSE_ACTIONS,
    CONTAINER_ACTIONS,
    PRUNE_TARGETS,
    SIGNALS,
    get_docker,
    invalidate,
    prune_argv,
    valid_container_ref,
    valid_image_ref,
    valid_name,
    valid_since,
)
from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()

DRIVER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")


def _context(request: Request):
    """Return (settings, docker) or fail when the module is switched off."""
    settings = request.app.state.settings
    if not settings.module_enabled("docker"):
        raise HTTPException(404, "Docker module is disabled")
    return settings, get_docker(settings, get_executor(settings))


def _audit(request: Request, settings, action: str) -> None:
    auth_log(
        settings,
        "docker",
        action,
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )


async def _payload(request: Request) -> dict[str, Any]:
    """Accept either a JSON body or a normal form submission."""
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
    return dict(form)


def _failure(message: str, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message}, status_code=status_code)


def _result(response: Any, message: str) -> JSONResponse:
    if not response.ok:
        return JSONResponse(
            {"ok": False, "output": response.output, "error": response.output or message}
        )
    return JSONResponse({"ok": True, "output": response.output, "message": message})


# ── Page ─────────────────────────────────────────────────────────────────────


@router.get("/docker", response_class=HTMLResponse)
def docker_page(request: Request):
    settings, docker = _context(request)
    state = docker.state()
    return render(
        request,
        "docker.html",
        state=state.as_dict(),
        compose_enabled=settings.docker.compose_enabled,
        stats_enabled=settings.docker.stats_enabled,
    )


# ── Reads ────────────────────────────────────────────────────────────────────


@router.get("/ajax/docker/status")
def docker_status(request: Request):
    settings, docker = _context(request)
    state = docker.state()
    payload: dict[str, Any] = {"ok": True, **state.as_dict()}
    if state.available:
        payload["df"] = docker.df().get("rows", [])
        payload["counts"] = docker.counts()
    return JSONResponse(payload)


@router.get("/ajax/docker/ps")
def docker_ps(request: Request, all: int = 1):
    _, docker = _context(request)
    state = docker.state()
    if not state.available:
        return JSONResponse(
            {"ok": True, "available": False, "reason": state.reason, "containers": []}
        )
    return JSONResponse(
        {
            "ok": True,
            "available": True,
            "containers": docker.containers(all_containers=bool(all)),
        }
    )


@router.get("/ajax/docker/containers/{ref}")
def docker_inspect(request: Request, ref: str):
    _, docker = _context(request)
    if not valid_container_ref(ref):
        return _failure("Invalid container reference")
    data = docker.inspect(ref)
    if not data.get("ok"):
        return JSONResponse({"ok": False, "error": data.get("error", "inspect failed")})
    data.pop("ok", None)
    return JSONResponse({"ok": True, "container": data})


@router.get("/ajax/docker/containers/{ref}/logs")
def docker_logs(
    request: Request, ref: str, lines: int = 300, since: str = "", timestamps: int = 0
):
    _, docker = _context(request)
    if not valid_container_ref(ref):
        return _failure("Invalid container reference")
    if since and not valid_since(since):
        return _failure("Invalid since value")
    return JSONResponse(
        docker.logs(ref, lines=lines, since=since, timestamps=bool(timestamps))
    )


@router.get("/ajax/docker/containers/{ref}/stats")
def docker_stats(request: Request, ref: str):
    _, docker = _context(request)
    if not valid_container_ref(ref):
        return _failure("Invalid container reference")
    return JSONResponse(docker.stats(ref))


@router.get("/ajax/docker/images")
def docker_images(request: Request, all: int = 0):
    _, docker = _context(request)
    if not docker.state().available:
        return JSONResponse({"ok": True, "available": False, "images": []})
    return JSONResponse(
        {"ok": True, "available": True, "images": docker.images(all_images=bool(all))}
    )


@router.get("/ajax/docker/volumes")
def docker_volumes(request: Request):
    _, docker = _context(request)
    return JSONResponse({"ok": True, "volumes": docker.volumes()})


@router.get("/ajax/docker/networks")
def docker_networks(request: Request):
    _, docker = _context(request)
    return JSONResponse({"ok": True, "networks": docker.networks()})


@router.get("/ajax/docker/networks/{name}/inspect")
def docker_network_inspect(request: Request, name: str):
    _, docker = _context(request)
    if not valid_name(name):
        return _failure("Invalid network name")
    data = docker.network_inspect(name)
    if not data.get("ok"):
        return JSONResponse({"ok": False, "error": data.get("error", "inspect failed")})
    data.pop("ok", None)
    return JSONResponse({"ok": True, "network": data})


@router.get("/ajax/docker/compose")
def docker_compose(request: Request):
    settings, docker = _context(request)
    return JSONResponse(
        {
            "ok": True,
            "enabled": settings.docker.compose_enabled,
            "available": bool(docker.state().compose),
            "projects": docker.compose_projects(),
        }
    )


@router.get("/ajax/docker/compose/{project}/file")
def docker_compose_file(request: Request, project: str):
    _, docker = _context(request)
    if not valid_name(project):
        return _failure("Invalid project name")
    data = docker.compose_file(project)
    if not data.get("ok"):
        return JSONResponse(
            {
                "ok": False,
                "error": data.get("error", "Cannot read file"),
                "reason": data.get("reason", ""),
                "path": data.get("path", ""),
            }
        )
    return JSONResponse(data)


@router.get("/ajax/docker/df")
def docker_df(request: Request):
    _, docker = _context(request)
    return JSONResponse(docker.df())


@router.get("/ajax/docker/info")
def docker_info(request: Request):
    _, docker = _context(request)
    return JSONResponse(docker.info())


@router.get("/ajax/docker/events")
def docker_events(request: Request, minutes: int = 60):
    _, docker = _context(request)
    return JSONResponse({"ok": True, "events": docker.events(minutes=minutes)})


@router.get("/ajax/docker/task/{task_id}")
def docker_task(request: Request, task_id: str):
    _, docker = _context(request)
    return JSONResponse(docker.task_status(task_id))


# ── Container actions ────────────────────────────────────────────────────────


@router.post("/ajax/docker/action")
async def docker_action(request: Request):
    settings, docker = _context(request)
    payload = await _payload(request)
    ref = str(payload.get("id", "")).strip()
    act = str(payload.get("act", "")).strip()

    if not valid_container_ref(ref):
        return _failure("Invalid container reference")

    if act == "kill":
        signal = str(payload.get("signal", "SIGTERM")).strip().upper()
        if signal not in SIGNALS:
            return _failure("Invalid signal")
        args = ["kill", "--signal", signal, ref]
    elif act == "rename":
        new_name = str(payload.get("name", "")).strip()
        if not valid_name(new_name):
            return _failure("Invalid container name")
        args = ["rename", ref, new_name]
    elif act in CONTAINER_ACTIONS:
        args = [*CONTAINER_ACTIONS[act], ref]
    else:
        return _failure("Unknown action")

    _audit(request, settings, f"{act} {ref}")
    result = docker.run(*args, timeout=90)
    invalidate()
    return _result(result, f"{act} done")


# ── Image actions ────────────────────────────────────────────────────────────


@router.post("/ajax/docker/image/action")
async def docker_image_action(request: Request):
    settings, docker = _context(request)
    payload = await _payload(request)
    act = str(payload.get("act", "")).strip()
    ref = str(payload.get("ref", "")).strip()

    if act == "pull":
        if not valid_image_ref(ref):
            return _failure("Invalid image reference")
        _audit(request, settings, f"pull {ref}")
        return JSONResponse(docker.start_task(docker.argv("pull", ref), f"pull {ref}"))

    if act == "prune":
        _audit(request, settings, "image prune")
        return JSONResponse(
            docker.start_task(docker.argv("image", "prune", "-f"), "image prune")
        )

    if not valid_image_ref(ref):
        return _failure("Invalid image reference")

    if act == "rm":
        _audit(request, settings, f"image rm {ref}")
        result = docker.image_remove(ref, force=bool(payload.get("force")))
        invalidate()
        return _result(result, "image removed")

    if act == "tag":
        target = str(payload.get("target", "")).strip()
        if not valid_image_ref(target):
            return _failure("Invalid target reference")
        _audit(request, settings, f"image tag {ref} {target}")
        result = docker.image_tag(ref, target)
        invalidate()
        return _result(result, "image tagged")

    if act == "inspect":
        result = docker.run("image", "inspect", ref, timeout=20)
        if not result.ok:
            return JSONResponse({"ok": False, "error": result.output})
        return JSONResponse({"ok": True, "output": result.stdout})

    return _failure("Unknown action")


# ── Volumes / networks ───────────────────────────────────────────────────────


@router.post("/ajax/docker/volume/action")
async def docker_volume_action(request: Request):
    settings, docker = _context(request)
    payload = await _payload(request)
    act = str(payload.get("act", "")).strip()
    name = str(payload.get("name", "")).strip()

    if act == "prune":
        _audit(request, settings, "volume prune")
        return JSONResponse(
            docker.start_task(docker.argv("volume", "prune", "-f"), "volume prune")
        )

    if not valid_name(name):
        return _failure("Invalid volume name")

    if act == "create":
        _audit(request, settings, f"volume create {name}")
        result = docker.run("volume", "create", name, timeout=30)
    elif act == "rm":
        _audit(request, settings, f"volume rm {name}")
        result = docker.run("volume", "rm", name, timeout=30)
    else:
        return _failure("Unknown action")
    invalidate()
    return _result(result, f"volume {act} done")


@router.post("/ajax/docker/network/action")
async def docker_network_action(request: Request):
    settings, docker = _context(request)
    payload = await _payload(request)
    act = str(payload.get("act", "")).strip()
    name = str(payload.get("name", "")).strip()

    if act == "prune":
        _audit(request, settings, "network prune")
        return JSONResponse(
            docker.start_task(docker.argv("network", "prune", "-f"), "network prune")
        )

    if not valid_name(name):
        return _failure("Invalid network name")

    if act == "create":
        driver = str(payload.get("driver", "bridge")).strip() or "bridge"
        if not DRIVER_RE.match(driver):
            return _failure("Invalid driver")
        _audit(request, settings, f"network create {name} ({driver})")
        result = docker.run("network", "create", "--driver", driver, name, timeout=30)
    elif act == "rm":
        _audit(request, settings, f"network rm {name}")
        result = docker.run("network", "rm", name, timeout=30)
    else:
        return _failure("Unknown action")
    invalidate()
    return _result(result, f"network {act} done")


# ── Compose ──────────────────────────────────────────────────────────────────


@router.post("/ajax/docker/compose/action")
async def docker_compose_action(request: Request):
    settings, docker = _context(request)
    if not settings.docker.compose_enabled:
        return _failure("Compose actions are disabled in config.toml", 403)

    payload = await _payload(request)
    project = str(payload.get("project", "")).strip()
    act = str(payload.get("act", "")).strip()
    if not valid_name(project):
        return _failure("Invalid project name")
    if act not in COMPOSE_ACTIONS:
        return _failure("Unknown action")

    args, error = docker.compose_argv(
        project, act, remove_volumes=bool(payload.get("remove_volumes"))
    )
    if error:
        if error.startswith("EACCES:"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": error,
                    "reason": "denied",
                    "path": error.split(":", 1)[1].strip(),
                },
                status_code=403,
            )
        return _failure(error)
    _audit(request, settings, f"compose {act} {project}")
    return JSONResponse(
        docker.start_task(docker.argv(*args), f"compose {act} {project}")
    )


# ── System ───────────────────────────────────────────────────────────────────


@router.post("/ajax/docker/system/prune")
async def docker_prune(request: Request):
    settings, docker = _context(request)
    payload = await _payload(request)
    raw_targets = payload.get("targets", [])
    if isinstance(raw_targets, str):
        raw_targets = [part for part in raw_targets.split(",") if part]
    targets = [str(item) for item in raw_targets if str(item) in PRUNE_TARGETS]
    if not targets:
        return _failure("No prune target selected")

    if str(payload.get("preview", "")).lower() in {"1", "true", "yes"}:
        table = docker.df()
        labels = {
            "containers": "Containers",
            "images": "Images",
            "volumes": "Local Volumes",
            "buildcache": "Build Cache",
            "networks": "Networks",
        }
        wanted = {labels[target] for target in targets}
        rows = [row for row in table.get("rows", []) if row["type"] in wanted]
        return JSONResponse({"ok": True, "preview": True, "rows": rows})

    argv, error = prune_argv(docker, targets)
    if error:
        return _failure(error)

    res = docker.start_task(argv, "prune " + ",".join(targets))
    if res.get("ok"):
        _audit(request, settings, f"prune {','.join(targets)}")
        res["risky"] = any(
            target in {"images", "volumes", "containers"} for target in targets
        )
    return JSONResponse(res)
