from __future__ import annotations

import re
import shlex

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()

ID_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")
ACTION_MAP = {
    "start": "start",
    "stop": "stop",
    "restart": "restart",
    "pause": "pause",
    "unpause": "unpause",
    "remove": "rm -f",
}
PS_FORMAT = r"{{.ID}}\t{{.Names}}\t{{.Image}}\t{{.State}}\t{{.Status}}\t{{.Ports}}"
IMAGES_FORMAT = r"{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}"


async def _payload(request: Request) -> dict:
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


def docker_ps(executor) -> dict:
    result = executor.run(
        f"sudo docker ps -a --format {shlex.quote(PS_FORMAT)} 2>&1", timeout=30
    )
    output = result.output
    lowered = output.lower()
    if "command not found" in lowered or "not installed" in lowered:
        return {"ok": True, "available": False, "reason": "Docker is not installed.",
                "containers": []}
    if "cannot connect to the docker daemon" in lowered:
        return {"ok": True, "available": False,
                "reason": "Docker daemon is not running.", "containers": []}
    if not result.ok:
        return {"ok": True, "available": False,
                "reason": output or "Docker command failed.", "containers": []}

    containers: list[dict] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        containers.append(
            {
                "id": fields[0],
                "name": fields[1],
                "image": fields[2],
                "state": fields[3],
                "status": fields[4],
                "ports": fields[5] if len(fields) > 5 else "",
            }
        )
    return {"ok": True, "available": True, "reason": "", "containers": containers}


def docker_images(executor) -> list[dict]:
    result = executor.run(
        f"sudo docker images --format {shlex.quote(IMAGES_FORMAT)} 2>&1", timeout=30
    )
    images: list[dict] = []
    if not result.ok:
        return images
    for line in result.output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 4:
            continue
        images.append(
            {"repo": fields[0], "tag": fields[1], "id": fields[2], "size": fields[3]}
        )
    return images


@router.get("/docker", response_class=HTMLResponse)
def docker_page(request: Request):
    return render(request, "docker.html")


@router.get("/ajax/docker/ps")
def docker_ps_endpoint(request: Request):
    settings = request.app.state.settings
    return JSONResponse(docker_ps(get_executor(settings)))


@router.post("/ajax/docker/action")
async def docker_action(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    payload = await _payload(request)
    container_id = str(payload.get("id", "")).strip()
    act = str(payload.get("act", ""))

    if not ID_RE.match(container_id):
        return JSONResponse({"ok": False, "error": "Invalid container id"}, status_code=400)
    if act not in ACTION_MAP:
        return JSONResponse({"ok": False, "error": "Unknown action"}, status_code=400)

    result = executor.run(
        f"sudo docker {ACTION_MAP[act]} {shlex.quote(container_id)} 2>&1", timeout=60
    )
    auth_log(settings, "docker", f"{act} {container_id}", client_ip(request, settings),
             request.headers.get("user-agent", ""))
    if not result.ok:
        return JSONResponse(
            {"ok": False, "output": result.output,
             "error": result.output or "Docker command failed"}
        )
    return JSONResponse(
        {"ok": True, "output": result.output, "message": f"{act.capitalize()} done"}
    )


@router.get("/ajax/docker/logs")
def docker_logs(request: Request, id: str = "", lines: int = 300):
    settings = request.app.state.settings
    executor = get_executor(settings)
    if not ID_RE.match(id.strip()):
        return JSONResponse({"ok": False, "error": "Invalid container id"}, status_code=400)
    lines = max(10, min(2000, lines))
    result = executor.run(
        f"sudo docker logs --tail {lines} {shlex.quote(id.strip())} 2>&1", timeout=30
    )
    if not result.ok:
        return JSONResponse(
            {"ok": False, "output": result.output,
             "error": result.output or "Docker command failed"}
        )
    return JSONResponse({"ok": True, "output": result.output})


@router.get("/ajax/docker/images")
def docker_images_endpoint(request: Request):
    settings = request.app.state.settings
    return JSONResponse({"ok": True, "images": docker_images(get_executor(settings))})
