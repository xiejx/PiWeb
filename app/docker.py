"""Docker engine access: command building, parsing, caching and background tasks.

Everything Docker-related in PiWeb goes through this module so the router stays
thin and the parsing logic is testable without a running daemon.

Two rules shape the implementation:

* Commands are built as **argument lists**, never shell strings. Container names
  and image references therefore cannot be interpreted by a shell.
* Output is read from ``--format '{{json .}}'`` / ``inspect`` — structured JSON,
  one object per line — instead of splitting tab-separated columns, which
  breaks as soon as a port mapping or image name contains a separator.
"""

from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .executor import CommandResult, Executor

# ── Input validation ─────────────────────────────────────────────────────────

CONTAINER_ID_RE = re.compile(r"^[a-f0-9]{6,64}$")
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$")
# Image reference grammar (see github.com/distribution/reference): an optional
# registry host with port, slash separated path components, an optional tag and
# an optional digest.
_ALNUM = r"[a-z0-9]+"
_PATH_COMPONENT = rf"{_ALNUM}(?:(?:[._]|__|[-]+){_ALNUM})*"
_DOMAIN_PART = r"(?:[a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9])"
_DOMAIN = rf"{_DOMAIN_PART}(?:\.{_DOMAIN_PART})*(?::[0-9]{{1,5}})?"
_TAG = r"[\w][\w.-]{0,127}"
IMAGE_RE = re.compile(
    rf"^(?:{_DOMAIN}/)?{_PATH_COMPONENT}(?:/{_PATH_COMPONENT})*"
    rf"(?::{_TAG})?(?:@sha256:[a-f0-9]{{64}})?$"
)
TASK_ID_RE = re.compile(r"^[0-9]{8,20}-[0-9a-f]{6}$")
SINCE_RE = re.compile(r"^(\d{1,6}[smhd]|\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?)$")

SIGNALS = {
    "SIGTERM",
    "SIGKILL",
    "SIGINT",
    "SIGHUP",
    "SIGQUIT",
    "SIGUSR1",
    "SIGUSR2",
}

CONTAINER_ACTIONS: dict[str, list[str]] = {
    "start": ["start"],
    "stop": ["stop"],
    "restart": ["restart"],
    "pause": ["pause"],
    "unpause": ["unpause"],
    "rm": ["rm"],
    "rm_force": ["rm", "-f"],
}

COMPOSE_ACTIONS = {"up", "stop", "restart", "down"}

PRUNE_TARGETS = {"containers", "images", "volumes", "buildcache", "networks"}

SENSITIVE_HINTS = ("PASS", "SECRET", "TOKEN", "KEY", "CREDENTIAL", "AUTH")

TASK_SENTINEL = "__PIWEB_TASK_DONE__"

# stderr fragments that identify why the engine is unusable
_NOT_INSTALLED = (
    "no such file or directory: 'docker'",
    "docker: not found",
    "docker: command not found",
    "command not found",
)
_NO_DAEMON = (
    "cannot connect to the docker daemon",
    "is the docker daemon running",
    "failed to connect to the docker api",
)
_NO_PERMISSION = (
    "permission denied while trying to connect",
    "got permission denied",
    "permission denied",
)
_NO_COMPOSE = ("is not a docker command", "unknown command: docker compose")


# ── Helpers ──────────────────────────────────────────────────────────────────


def valid_container_ref(value: str) -> bool:
    """Container references are either a short/full id or a container name."""
    value = value or ""
    return bool(CONTAINER_ID_RE.match(value) or NAME_RE.match(value))


def valid_name(value: str) -> bool:
    return bool(NAME_RE.match(value or ""))


def valid_image_ref(value: str) -> bool:
    return bool(IMAGE_RE.match(value or ""))


def valid_since(value: str) -> bool:
    return bool(SINCE_RE.match((value or "").strip()))


def _short_id(value: str) -> str:
    value = value or ""
    return value[:12] if len(value) > 12 else value


def _labels(raw: Any) -> dict[str, str]:
    """Docker prints labels as ``k=v,k=v``; some buildx versions print JSON."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if not isinstance(raw, str):
        return {}
    if raw.strip().startswith("{"):
        try:
            parsed = json.loads(raw)
        except ValueError:
            return {}
        return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
    labels: dict[str, str] = {}
    for chunk in raw.split(","):
        if "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        labels[key.strip()] = val.strip()
    return labels


def _health_from_status(status: str) -> str:
    match = re.search(r"\((healthy|unhealthy|health: starting)\)", status or "")
    if not match:
        return ""
    value = match.group(1)
    return "starting" if value.startswith("health:") else value


def _json_lines(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _mask_env(env: list[str]) -> list[str]:
    masked: list[str] = []
    for item in env:
        key, sep, _ = str(item).partition("=")
        if sep and any(hint in key.upper() for hint in SENSITIVE_HINTS):
            masked.append(f"{key}=***")
        else:
            masked.append(str(item))
    return masked


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return default


# ── Short-lived cache ────────────────────────────────────────────────────────

_CACHE: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str, ttl: float) -> Any:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    stamp, value = entry
    if time.monotonic() - stamp > ttl:
        return None
    return value


def _cache_put(key: str, value: Any) -> Any:
    _CACHE[key] = (time.monotonic(), value)
    return value


def invalidate(prefix: str = "") -> None:
    """Drop cached reads; called after any write so the UI is never stale."""
    if not prefix:
        _CACHE.clear()
        return
    for key in [key for key in _CACHE if key.startswith(prefix)]:
        _CACHE.pop(key, None)


# ── State ────────────────────────────────────────────────────────────────────


@dataclass
class DockerState:
    available: bool = False
    reason: str = ""
    version: str = ""
    compose: str = ""
    privileged: str = "group"

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "version": self.version,
            "compose": self.compose,
            "privileged": self.privileged,
        }


class Docker:
    """Thin, validated wrapper around the docker CLI for one executor."""

    def __init__(self, settings: Settings, executor: Executor) -> None:
        self.settings = settings
        self.executor = executor
        self.config = settings.docker

    # -- plumbing ------------------------------------------------------------

    def argv(self, *args: str) -> list[str]:
        prefix = ["sudo"] if self.config.privileged == "sudo" else []
        return [*prefix, "docker", *[str(arg) for arg in args]]

    def run(self, *args: str, timeout: float = 30.0) -> CommandResult:
        return self.executor.run_argv(self.argv(*args), timeout=timeout)

    def state(self, refresh: bool = False) -> DockerState:
        if not refresh:
            cached = _cache_get("state", 10.0)
            if isinstance(cached, DockerState):
                return cached

        result = self.run("version", "--format", "{{.Server.Version}}", timeout=10)
        text = result.output
        state = DockerState(privileged=self.config.privileged)

        if result.ok and result.stdout.strip():
            state.available = True
            state.version = result.stdout.strip().splitlines()[0]
            compose = self.run("compose", "version", "--short", timeout=10)
            if compose.ok and compose.stdout.strip():
                state.compose = compose.stdout.strip().splitlines()[0]
            elif any(hint in compose.output.lower() for hint in _NO_COMPOSE):
                state.compose = ""
        else:
            lowered = text.lower()
            if any(hint in lowered for hint in _NO_PERMISSION):
                state.reason = "permission"
            elif any(hint in lowered for hint in _NO_DAEMON):
                state.reason = "daemon"
            elif any(hint in lowered for hint in _NOT_INSTALLED):
                state.reason = "missing"
            else:
                state.reason = "error"
        return _cache_put("state", state)

    def available(self) -> bool:
        return self.state().available

    # -- containers ----------------------------------------------------------

    def containers(self, all_containers: bool = True, refresh: bool = False) -> list[dict[str, Any]]:
        key = f"ps:{int(bool(all_containers))}"
        if not refresh:
            cached = _cache_get(key, 2.0)
            if cached is not None:
                return cached
        state = self.state()
        if not state.available:
            return []

        args = ["ps"]
        if all_containers:
            args.append("-a")
        args += ["--no-trunc", "--format", "{{json .}}"]
        result = self.run(*args, timeout=30)
        if not result.ok:
            return []

        rows: list[dict[str, Any]] = []
        for item in _json_lines(result.stdout):
            labels = _labels(item.get("Labels"))
            status = str(item.get("Status", ""))
            rows.append(
                {
                    "id": str(item.get("ID", "")),
                    "short_id": _short_id(str(item.get("ID", ""))),
                    "name": str(item.get("Names", "")).split(",")[0],
                    "image": str(item.get("Image", "")),
                    "command": str(item.get("Command", "")),
                    "state": str(item.get("State", "")),
                    "status": status,
                    "health": _health_from_status(status),
                    "ports": str(item.get("Ports", "")),
                    "created_at": str(item.get("CreatedAt", "")),
                    "running_for": str(item.get("RunningFor", "")),
                    "project": labels.get("com.docker.compose.project", ""),
                    "service": labels.get("com.docker.compose.service", ""),
                    "networks": str(item.get("Networks", "")),
                    "mounts": str(item.get("Mounts", "")),
                    "labels": labels,
                }
            )
        rows.sort(key=lambda row: (row["state"] != "running", row["name"].lower()))
        return _cache_put(key, rows)

    def inspect(self, ref: str) -> dict[str, Any]:
        result = self.run("inspect", "--format", "{{json .}}", ref, timeout=20)
        if not result.ok:
            return {"ok": False, "error": result.output or "inspect failed"}
        line = result.stdout.strip()
        try:
            raw = json.loads(line)
        except ValueError:
            return {"ok": False, "error": "Unexpected inspect output"}
        if not isinstance(raw, dict):
            return {"ok": False, "error": "Unexpected inspect output"}

        state = raw.get("State") or {}
        health = (state.get("Health") or {}) if isinstance(state.get("Health"), dict) else {}
        config = raw.get("Config") or {}
        host = raw.get("HostConfig") or {}
        restart = (host.get("RestartPolicy") or {}).get("Name", "") if isinstance(host.get("RestartPolicy"), dict) else ""
        networks: list[dict[str, str]] = []
        network_settings = raw.get("NetworkSettings") or {}
        for name, detail in (network_settings.get("Networks") or {}).items():
            detail = detail or {}
            networks.append(
                {
                    "name": str(name),
                    "ip": str(detail.get("IPAddress", "")),
                    "gateway": str(detail.get("Gateway", "")),
                    "mac": str(detail.get("MacAddress", "")),
                }
            )
        mounts: list[dict[str, Any]] = []
        for mount in raw.get("Mounts") or []:
            mounts.append(
                {
                    "type": str(mount.get("Type", "")),
                    "source": str(mount.get("Source", mount.get("Name", ""))),
                    "destination": str(mount.get("Destination", "")),
                    "mode": str(mount.get("Mode", "")),
                    "rw": bool(mount.get("RW", True)),
                }
            )
        health_log = []
        for entry in (health.get("Log") or [])[-3:]:
            health_log.append(
                {
                    "start": str(entry.get("Start", "")),
                    "exit_code": _to_int(entry.get("ExitCode")),
                    "output": str(entry.get("Output", "")).strip(),
                }
            )
        return {
            "ok": True,
            "id": str(raw.get("Id", "")),
            "name": str(raw.get("Name", "")).lstrip("/"),
            "image": str(config.get("Image", raw.get("Image", ""))),
            "created": str(raw.get("Created", "")),
            "platform": str(raw.get("Platform", "")),
            "restart_policy": str(restart or "no"),
            "state": {
                "status": str(state.get("Status", "")),
                "running": bool(state.get("Running")),
                "paused": bool(state.get("Paused")),
                "restarting": bool(state.get("Restarting")),
                "exit_code": _to_int(state.get("ExitCode")),
                "error": str(state.get("Error", "")),
                "started_at": str(state.get("StartedAt", "")),
                "finished_at": str(state.get("FinishedAt", "")),
                "health": str(health.get("Status", "")),
            },
            "health_log": health_log,
            "config": {
                "cmd": [str(part) for part in (config.get("Cmd") or [])],
                "entrypoint": [str(part) for part in (config.get("Entrypoint") or [])],
                "env": _mask_env([str(item) for item in (config.get("Env") or [])]),
                "labels": {str(k): str(v) for k, v in (config.get("Labels") or {}).items()},
                "working_dir": str(config.get("WorkingDir", "")),
                "user": str(config.get("User", "")),
            },
            "mounts": mounts,
            "networks": networks,
        }

    def logs(
        self,
        ref: str,
        lines: int = 300,
        since: str = "",
        timestamps: bool = False,
    ) -> dict[str, Any]:
        lines = max(10, min(self.config.log_max_lines, int(lines)))
        args = ["logs", "--tail", str(lines)]
        if since and valid_since(since):
            args += ["--since", since]
        if timestamps:
            args.append("--timestamps")
        args.append(ref)
        result = self.run(*args, timeout=30)
        return {
            "ok": result.ok,
            "output": result.output,
            "lines": lines,
            "error": "" if result.ok else (result.output or "docker logs failed"),
        }

    def stats(self, ref: str) -> dict[str, Any]:
        if not self.config.stats_enabled:
            return {"ok": True, "available": False, "reason": "disabled"}
        result = self.run("stats", "--no-stream", "--format", "{{json .}}", ref, timeout=20)
        if not result.ok:
            return {"ok": False, "available": False, "error": result.output}
        rows = _json_lines(result.stdout)
        if not rows:
            return {"ok": True, "available": False, "reason": "no data"}
        item = rows[0]
        mem_usage = str(item.get("MemUsage", ""))
        # cgroup v2 hosts (and some Pi kernels) report 0B here; say so instead
        # of drawing a flat zero line.
        usable = bool(mem_usage) and not mem_usage.startswith("0B / 0B")
        return {
            "ok": True,
            "available": usable,
            "reason": "" if usable else "unsupported",
            "cpu": _to_float(item.get("CPUPerc")),
            "mem_percent": _to_float(item.get("MemPerc")),
            "mem_usage": mem_usage,
            "net_io": str(item.get("NetIO", "")),
            "block_io": str(item.get("BlockIO", "")),
            "pids": _to_int(item.get("PIDs")),
        }

    # -- images --------------------------------------------------------------

    def images(self, all_images: bool = False, refresh: bool = False) -> list[dict[str, Any]]:
        key = f"images:{int(bool(all_images))}"
        if not refresh:
            cached = _cache_get(key, 3.0)
            if cached is not None:
                return cached
        if not self.state().available:
            return []
        args = ["images"]
        if all_images:
            args.append("-a")
        args += ["--format", "{{json .}}"]
        result = self.run(*args, timeout=30)
        if not result.ok:
            return []
        rows: list[dict[str, Any]] = []
        for item in _json_lines(result.stdout):
            repo = str(item.get("Repository", ""))
            tag = str(item.get("Tag", ""))
            ref = "<none>" if repo == "<none>" else (repo if tag in {"", "<none>"} else f"{repo}:{tag}")
            rows.append(
                {
                    "id": str(item.get("ID", "")),
                    "short_id": _short_id(str(item.get("ID", ""))),
                    "repository": repo,
                    "tag": tag,
                    "ref": ref,
                    "digest": str(item.get("Digest", "")),
                    "created": str(item.get("CreatedSince", "")),
                    "size": str(item.get("Size", "")),
                    "containers": _to_int(item.get("Containers")),
                    "dangling": repo == "<none>",
                }
            )
        rows.sort(key=lambda row: (row["dangling"], row["ref"].lower()))
        return _cache_put(key, rows)

    def image_remove(self, ref: str, force: bool = False) -> CommandResult:
        args = ["image", "rm"]
        if force:
            args.append("-f")
        args.append(ref)
        return self.run(*args, timeout=60)

    def image_tag(self, source: str, target: str) -> CommandResult:
        return self.run("tag", source, target, timeout=30)

    # -- volumes / networks --------------------------------------------------

    def volumes(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh:
            cached = _cache_get("volumes", 3.0)
            if cached is not None:
                return cached
        if not self.state().available:
            return []
        result = self.run("volume", "ls", "--format", "{{json .}}", timeout=20)
        if not result.ok:
            return []
        rows = [
            {
                "name": str(item.get("Name", "")),
                "driver": str(item.get("Driver", "")),
                "mountpoint": str(item.get("Mountpoint", "")),
                "scope": str(item.get("Scope", "")),
            }
            for item in _json_lines(result.stdout)
        ]
        rows.sort(key=lambda row: row["name"].lower())
        return _cache_put("volumes", rows)

    def networks(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh:
            cached = _cache_get("networks", 3.0)
            if cached is not None:
                return cached
        if not self.state().available:
            return []
        result = self.run("network", "ls", "--format", "{{json .}}", timeout=20)
        if not result.ok:
            return []
        rows = [
            {
                "id": str(item.get("ID", "")),
                "short_id": _short_id(str(item.get("ID", ""))),
                "name": str(item.get("Name", "")),
                "driver": str(item.get("Driver", "")),
                "scope": str(item.get("Scope", "")),
                "ipv6": str(item.get("IPv6", "")),
                "internal": str(item.get("Internal", "")),
            }
            for item in _json_lines(result.stdout)
        ]
        rows.sort(key=lambda row: row["name"].lower())
        return _cache_put("networks", rows)

    def network_inspect(self, name: str) -> dict[str, Any]:
        result = self.run("network", "inspect", "--format", "{{json .}}", name, timeout=20)
        if not result.ok:
            return {"ok": False, "error": result.output or "inspect failed"}
        try:
            raw = json.loads(result.stdout.strip())
        except ValueError:
            return {"ok": False, "error": "Unexpected inspect output"}
        if not isinstance(raw, dict):
            return {"ok": False, "error": "Unexpected inspect output"}

        ipam = raw.get("IPAM") or {}
        subnets = [
            {
                "subnet": str(item.get("Subnet", "")),
                "gateway": str(item.get("Gateway", "")),
                "ip_range": str(item.get("IPRange", "")),
                "aux_addresses": {
                    str(host): str(ip) for host, ip in (item.get("AuxiliaryAddresses") or {}).items()
                },
            }
            for item in (ipam.get("Config") or [])
            if isinstance(item, dict)
        ]
        containers = []
        attached = raw.get("Containers") or {}
        for cid, detail in attached.items():
            detail = detail or {}
            containers.append(
                {
                    "id": str(cid),
                    "name": str(detail.get("Name", "")),
                    "ipv4": str(detail.get("IPv4Address", "")),
                    "ipv6": str(detail.get("IPv6Address", "")),
                    "mac": str(detail.get("MacAddress", "")),
                }
            )
        containers.sort(key=lambda row: row["name"].lower())
        return {
            "ok": True,
            "id": str(raw.get("Id", "")),
            "name": str(raw.get("Name", "")),
            "driver": str(raw.get("Driver", "")),
            "scope": str(raw.get("Scope", "")),
            "created": str(raw.get("Created", "")),
            "ipv6": bool(raw.get("EnableIPv6")),
            "internal": bool(raw.get("Internal")),
            "attachable": bool(raw.get("Attachable")),
            "ingress": bool(raw.get("Ingress")),
            "ipam_driver": str(ipam.get("Driver", "")),
            "subnets": subnets,
            "containers": containers,
            "options": {str(k): str(v) for k, v in (raw.get("Options") or {}).items()},
            "labels": {str(k): str(v) for k, v in (raw.get("Labels") or {}).items()},
        }

    # -- system --------------------------------------------------------------

    def df(self) -> dict[str, Any]:
        result = self.run("system", "df", "--format", "{{json .}}", timeout=20)
        if not result.ok:
            return {"ok": False, "error": result.output, "rows": []}
        rows = [
            {
                "type": str(item.get("Type", "")),
                "total": _to_int(item.get("TotalCount")),
                "active": _to_int(item.get("Active")),
                "size": str(item.get("Size", "")),
                "reclaimable": str(item.get("Reclaimable", "")),
            }
            for item in _json_lines(result.stdout)
        ]
        return {"ok": True, "rows": rows}

    def info(self) -> dict[str, Any]:
        result = self.run("info", "--format", "{{json .}}", timeout=20)
        if not result.ok:
            return {"ok": False, "error": result.output}
        try:
            raw = json.loads(result.stdout.strip() or "{}")
        except ValueError:
            return {"ok": False, "error": "Unexpected info output"}
        swarm = raw.get("Swarm") or {}
        return {
            "ok": True,
            "server_version": str(raw.get("ServerVersion", "")),
            "driver": str(raw.get("Driver", "")),
            "cgroup_version": str(raw.get("CgroupVersion", "")),
            "root_dir": str(raw.get("DockerRootDir", "")),
            "os": str(raw.get("OperatingSystem", "")),
            "architecture": str(raw.get("Architecture", "")),
            "kernel": str(raw.get("KernelVersion", "")),
            "cpus": _to_int(raw.get("NCPU")),
            "memory_total": _to_int(raw.get("MemTotal")),
            "containers": _to_int(raw.get("Containers")),
            "running": _to_int(raw.get("ContainersRunning")),
            "paused": _to_int(raw.get("ContainersPaused")),
            "stopped": _to_int(raw.get("ContainersStopped")),
            "images": _to_int(raw.get("Images")),
            "swarm": str(swarm.get("LocalNodeState", "")),
        }

    def events(self, minutes: int = 60, limit: int = 80) -> list[dict[str, Any]]:
        minutes = max(1, min(24 * 60, int(minutes)))
        result = self.run(
            "events",
            "--since",
            f"{minutes}m",
            "--until",
            "0s",
            "--format",
            "{{json .}}",
            timeout=20,
        )
        if not result.ok:
            return []
        rows: list[dict[str, Any]] = []
        for item in _json_lines(result.stdout):
            actor = item.get("Actor") or {}
            attributes = actor.get("Attributes") or {}
            rows.append(
                {
                    "type": str(item.get("Type", "")),
                    "action": str(item.get("Action", item.get("status", ""))),
                    "name": str(attributes.get("name", "")),
                    "image": str(attributes.get("image", "")),
                    "time": _to_int(item.get("time")),
                }
            )
        rows.sort(key=lambda row: row["time"], reverse=True)
        return rows[:limit]

    # -- compose -------------------------------------------------------------

    def compose_projects(self, refresh: bool = False) -> list[dict[str, Any]]:
        if not refresh:
            cached = _cache_get("compose", 3.0)
            if cached is not None:
                return cached
        projects: dict[str, dict[str, Any]] = {}
        for container in self.containers(refresh=refresh):
            project = container.get("project") or ""
            if not project:
                continue
            entry = projects.setdefault(
                project,
                {
                    "name": project,
                    "dir": container["labels"].get("com.docker.compose.project.working_dir", ""),
                    "files": container["labels"].get("com.docker.compose.project.config_files", ""),
                    "services": [],
                    "total": 0,
                    "running": 0,
                },
            )
            entry["total"] += 1
            if container["state"] == "running":
                entry["running"] += 1
            entry["services"].append(
                {
                    "name": container.get("service") or container["name"],
                    "container": container["name"],
                    "state": container["state"],
                    "health": container["health"],
                }
            )
        rows = sorted(projects.values(), key=lambda item: item["name"].lower())
        return _cache_put("compose", rows)

    def _project(self, name: str) -> dict[str, Any] | None:
        for project in self.compose_projects():
            if project["name"] == name:
                return project
        return None

    def path_allowed(self, path: str) -> bool:
        """Compose paths come from Docker labels; a whitelist adds a second gate.

        With no ``compose_dirs`` configured the labels are trusted (only someone
        who can already start containers can set them). Configure the list to
        restrict the module to specific stacks.
        """
        dirs = [item for item in self.config.compose_dirs if item.strip()]
        if not dirs:
            return True
        if getattr(self.executor, "mode", "local") == "ssh":
            candidate = posixpath.normpath(path)
            for root in dirs:
                root_norm = posixpath.normpath(root)
                if candidate == root_norm or candidate.startswith(root_norm.rstrip("/") + "/"):
                    return True
            return False
        try:
            real = Path(path).resolve()
        except OSError:
            return False
        for root in dirs:
            try:
                root_real = Path(root).resolve()
            except OSError:
                continue
            if real == root_real or root_real in real.parents:
                return True
        return False

    def unreadable(self, path: str) -> bool:
        """True when the PiWeb user cannot read ``path`` in local mode.

        Compose files often live under a 0700 home directory, so the file itself
        may be world-readable while a parent directory blocks traversal.
        """
        if getattr(self.executor, "mode", "local") == "ssh":
            return False
        target = Path(path)
        try:
            if target.is_dir():
                with os.scandir(path) as entries:
                    next(entries, None)
            else:
                with open(path, "rb") as handle:
                    handle.read(1)
        except PermissionError:
            return True
        except OSError:
            return False
        return False

    def compose_argv(self, project: str, action: str, remove_volumes: bool = False) -> tuple[list[str], str]:
        """Return (argv, error). The argv excludes the ``docker`` prefix."""
        if action not in COMPOSE_ACTIONS:
            return [], "Unknown compose action"
        entry = self._project(project)
        if not entry:
            return [], "Unknown compose project"
        workdir = entry.get("dir") or ""
        files = entry.get("files") or ""
        if not workdir or not files:
            return [], "Compose project has no recorded working directory"

        for file_path in [part.strip() for part in files.split(",") if part.strip()]:
            if not self.path_allowed(file_path):
                return [], "Compose file is outside the allowed directories"
            if self.unreadable(file_path):
                return [], f"EACCES: {file_path}"
        if not self.path_allowed(workdir):
            return [], "Compose directory is outside the allowed directories"
        if self.unreadable(workdir):
            return [], f"EACCES: {workdir}"

        args = ["compose", "--project-directory", workdir]
        for file_path in [part.strip() for part in files.split(",") if part.strip()]:
            args += ["-f", file_path]
        args += ["-p", project]
        if action == "up":
            args += ["up", "-d"]
        elif action == "down":
            args += ["down"]
            if remove_volumes:
                args += ["--volumes"]
        else:
            args.append(action)
        return args, ""

    def compose_file(self, project: str) -> dict[str, Any]:
        entry = self._project(project)
        if not entry:
            return {"ok": False, "error": "Unknown compose project"}
        files = [part.strip() for part in (entry.get("files") or "").split(",") if part.strip()]
        if not files:
            return {"ok": False, "error": "Compose project has no recorded file"}
        path = files[0]
        if not self.path_allowed(path):
            return {"ok": False, "error": "Compose file is outside the allowed directories"}
        result = self.executor.run_argv(["cat", path], timeout=15)
        if not result.ok:
            payload = {"ok": False, "path": path, "error": result.output or "Cannot read compose file"}
            if self.unreadable(path) or "permission denied" in (result.output or "").lower():
                payload["reason"] = "denied"
            return payload
        content = result.stdout
        truncated = False
        if len(content) > 200_000:
            content = content[:200_000]
            truncated = True
        return {"ok": True, "path": path, "content": content, "truncated": truncated}

    # -- background tasks ----------------------------------------------------

    def task_dir(self) -> Path:
        return self.settings.data_dir / "docker_tasks"

    def start_task(self, argv: list[str], label: str) -> dict[str, Any]:
        """Run a long command detached and report progress through a log file."""
        task_id = time.strftime("%Y%m%d%H%M%S") + "-" + uuid.uuid4().hex[:6]
        directory = self.task_dir()
        directory.mkdir(parents=True, exist_ok=True)
        self._prune_tasks(directory)

        log_path = directory / f"{task_id}.log"
        command = f"{shlex.join(argv)}; printf '\\n{TASK_SENTINEL} rc=%s\\n' $?"
        result = self.executor.run_background(command, log_path)
        if not result.ok:
            return {"ok": False, "error": result.output or "Cannot start task"}

        try:
            (directory / f"{task_id}.json").write_text(
                json.dumps(
                    {
                        "label": label,
                        "argv": argv,
                        "started": int(time.time()),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError:
            pass
        invalidate()
        return {"ok": True, "id": task_id, "label": label}

    def task_status(self, task_id: str) -> dict[str, Any]:
        if not TASK_ID_RE.match(task_id or ""):
            return {"ok": False, "error": "Invalid task id"}
        directory = self.task_dir()
        log_path = directory / f"{task_id}.log"
        meta: dict[str, Any] = {}
        try:
            meta = json.loads((directory / f"{task_id}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}

        if not log_path.is_file():
            return {"ok": False, "error": "Unknown task", "id": task_id}

        try:
            text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"ok": False, "error": str(exc), "id": task_id}

        done = TASK_SENTINEL in text
        code = None
        if done:
            match = re.search(re.escape(TASK_SENTINEL) + r" rc=(\d+)", text)
            if match:
                code = int(match.group(1))
        body = text.split(TASK_SENTINEL)[0]
        tail = body[-8000:]
        return {
            "ok": True,
            "id": task_id,
            "label": str(meta.get("label", "")),
            "done": done,
            "success": bool(done and code == 0),
            "returncode": code,
            "output": tail,
        }

    def _prune_tasks(self, directory: Path) -> None:
        cutoff = time.time() - 7 * 86400
        try:
            for path in directory.iterdir():
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink(missing_ok=True)
        except OSError:
            pass

    # -- dashboard summary ---------------------------------------------------

    def counts(self) -> dict[str, int]:
        rows = self.containers()
        running = sum(1 for row in rows if row["state"] == "running")
        unhealthy = sum(
            1
            for row in rows
            if row["health"] == "unhealthy"
            or (row["state"] == "exited" and row.get("labels", {}).get("com.docker.compose.project"))
        )
        return {"total": len(rows), "running": running, "unhealthy": unhealthy}


def get_docker(settings: Settings, executor: Executor) -> Docker:
    return Docker(settings, executor)


def prune_argv(docker: Docker, targets: list[str]) -> tuple[list[str], str]:
    """Build the argv for a cleanup run; returns (argv, error)."""
    chosen = [target for target in targets if target in PRUNE_TARGETS]
    if not chosen:
        return [], "No prune target selected"
    if len(chosen) == 1:
        target = chosen[0]
        if target == "buildcache":
            return docker.argv("builder", "prune", "-f"), ""
        if target == "containers":
            return docker.argv("container", "prune", "-f"), ""
        if target == "networks":
            return docker.argv("network", "prune", "-f"), ""
        if target == "volumes":
            return docker.argv("volume", "prune", "-f"), ""
        return docker.argv("image", "prune", "-f", "--all"), ""
    # `system prune` covers containers, networks, images and build cache.
    if set(chosen) == {"containers", "images", "networks", "buildcache"}:
        return docker.argv("system", "prune", "-f"), ""
    return [], "Unsupported prune combination"
