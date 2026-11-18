from __future__ import annotations

import json
import os
import re
import shlex
import time
from pathlib import Path

from .config import BASE_DIR, Settings
from .executor import Executor

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
RUNNABLE_RE = re.compile(r"\.(sh|py)$")


def scripts_dir(settings: Settings) -> Path:
    return settings.scripts_dir


def examples_dir() -> Path:
    return BASE_DIR / "scripts" / "examples"


def name_valid(name: str) -> bool:
    if not name or len(name) > 64:
        return False
    if not NAME_RE.match(name):
        return False
    return bool(re.search(r"\.(sh|py|txt)$", name))


def resolve(settings: Settings, name: str, must_exist: bool) -> Path | None:
    """Validate a script name and return a path that cannot escape user_scripts."""
    if not name_valid(name):
        return None
    directory = scripts_dir(settings)
    path = directory / name
    if path.is_symlink():
        return None
    if path.exists():
        if not path.is_file():
            return None
        try:
            if directory.resolve() not in path.resolve().parents:
                return None
        except OSError:
            return None
    elif must_exist:
        return None
    return path


def list_scripts(settings: Settings) -> list[dict[str, object]]:
    directory = scripts_dir(settings)
    files: list[dict[str, object]] = []
    if directory.is_dir():
        for path in sorted(directory.iterdir(), key=lambda p: p.name):
            if not path.is_file() or not name_valid(path.name):
                continue
            stat = path.stat()
            files.append(
                {"name": path.name, "size": stat.st_size, "mtime": int(stat.st_mtime)}
            )
    return files


def list_examples() -> list[str]:
    directory = examples_dir()
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir() if p.is_file() and name_valid(p.name))


def load(settings: Settings, name: str, from_template: bool = False) -> str | None:
    if from_template:
        path = examples_dir() / name
        if not name_valid(name) or not path.is_file() or path.is_symlink():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    path = resolve(settings, name, must_exist=True)
    if path is None:
        return None
    return path.read_text(encoding="utf-8", errors="replace")


def save(settings: Settings, name: str, content: str) -> str:
    if not name_valid(name):
        return "Script names: letters, digits, . _ -, ending in .sh, .py or .txt"
    if len(content.encode("utf-8")) > 262144:
        return "Script too large (max 256 KB)"
    path = resolve(settings, name, must_exist=False)
    if path is None:
        return "Refusing to write: the target is not a regular file in user_scripts/"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if name.endswith((".sh", ".py")):
            path.chmod(0o750)
    except OSError:
        return "Could not write the file — is user_scripts/ writable by the service user?"
    return ""


def delete(settings: Settings, name: str) -> str:
    if not name_valid(name):
        return "Invalid script name"
    path = scripts_dir(settings) / name
    if not path.is_symlink() and not path.is_file():
        return "Script not found"
    try:
        path.unlink()
    except OSError:
        return "Could not delete the file"
    remaining = [p for p in pins(settings) if p != name]
    _write_pins(settings, remaining)
    return ""


def pins(settings: Settings) -> list[str]:
    pin_file = scripts_dir(settings) / ".pins.json"
    try:
        raw = json.loads(pin_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(raw, list):
        return []
    result = []
    for name in raw:
        if (
            isinstance(name, str)
            and RUNNABLE_RE.search(name)
            and resolve(settings, name, must_exist=True)
        ):
            result.append(name)
    return list(dict.fromkeys(result))


def _write_pins(settings: Settings, pins_list: list[str]) -> str:
    path = scripts_dir(settings) / ".pins.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(pins_list), encoding="utf-8")
    except OSError:
        return "Could not save — is user_scripts/ writable by the service user?"
    return ""


def set_pin(settings: Settings, name: str, pinned: bool) -> str:
    if not RUNNABLE_RE.search(name) or resolve(settings, name, must_exist=True) is None:
        return "Only saved .sh and .py scripts can be pinned"
    current = [p for p in pins(settings) if p != name]
    if pinned:
        if len(current) >= 6:
            return "At most 6 scripts can be pinned to the dashboard"
        current.append(name)
    return _write_pins(settings, current)


def run(
    settings: Settings,
    executor: Executor,
    name: str,
    background: bool = False,
    timeout: float = 60.0,
) -> dict[str, object]:
    if not name_valid(name):
        return {"ok": False, "error": "Invalid script name"}
    path = resolve(settings, name, must_exist=True)
    if path is None:
        return {"ok": False, "error": "Save the script first"}
    interpreter = "bash" if name.endswith(".sh") else "python3" if name.endswith(".py") else ""
    if not interpreter:
        return {"ok": False, "error": "Only .sh and .py scripts can be run"}

    if background:
        log_name = (
            time.strftime("%Y-%m-%d_%H-%M-%S")
            + "_"
            + re.sub(r"[^A-Za-z0-9_-]", "-", name)
            + ".log"
        )
        log_path = settings.command_logs_dir / log_name
        command = f"{interpreter} {shlex.quote(str(path))}"
        result = executor.run_background(command, log_path)
        if not result.ok:
            return {"ok": False, "error": result.stderr or "Run failed"}
        return {"ok": True, "log": log_name}

    result = executor.run(
        f"{interpreter} {shlex.quote(str(path))} 2>&1", timeout=timeout
    )
    if result.ok:
        return {"ok": True, "output": result.output}
    return {"ok": False, "error": result.output or "Run failed"}


def tile_output(
    settings: Settings, executor: Executor, name: str, force: bool = False
) -> dict[str, object]:
    if name not in pins(settings):
        return {"ok": False, "error": "Not a pinned script"}
    cache = settings.command_logs_dir / f".tile_{_md5(name)}.json"
    hit = None
    if cache.is_file():
        try:
            hit = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            hit = None
    if not force and isinstance(hit, dict) and time.time() - int(hit.get("time", 0)) < 60:
        return {
            "ok": True,
            "output": str(hit.get("output", "")),
            "failed": bool(hit.get("failed")),
            "time": int(hit.get("time", 0)),
            "cached": True,
        }

    path = resolve(settings, name, must_exist=True)
    if path is None:
        return {"ok": False, "error": "Script not found"}
    interpreter = "python3" if name.endswith(".py") else "bash"
    result = executor.run(
        f"timeout 15 {interpreter} {shlex.quote(str(path))} 2>&1", timeout=20
    )
    text = result.output if result.output else "(no output)"
    if len(text) > 4000:
        text = text[:4000] + "\n…"
    tile = {
        "output": text,
        "failed": not result.ok,
        "time": int(time.time()),
    }
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(tile), encoding="utf-8")
    except OSError:
        pass
    return {"ok": True, **tile, "cached": False}


def _md5(value: str) -> str:
    import hashlib

    return hashlib.md5(value.encode("utf-8")).hexdigest()
