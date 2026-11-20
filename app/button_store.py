from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from pathlib import Path

from .config import Settings
from .executor import Executor

STYLES = ["btn-default", "btn-primary", "btn-success", "btn-info", "btn-warning", "btn-danger"]
SIZES = ["btn-xs", "btn-sm", "btn-md", "btn-lg"]


def buttons_file(settings: Settings) -> Path:
    return settings.buttons_dir / "buttons.json"


def load(settings: Settings) -> list[dict] | None:
    path = buttons_file(settings)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [b for b in data if isinstance(b, dict)]


def save(settings: Settings, buttons: list[dict]) -> str:
    path = buttons_file(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(buttons, indent=2), encoding="utf-8")
    except OSError:
        return (
            f"Cannot write {path} — fix with: sudo chown -R "
            f"{settings.ssh_user}:{settings.ssh_user} {path.parent}"
        )
    return ""


def sanitize(post: dict) -> dict:
    icon = re.sub(r"[^a-z0-9\-]", "", str(post.get("button_icon", "")).lower())
    style = post.get("button_style", "")
    size = post.get("button_size", "")
    return {
        "button_title": str(post.get("button_title", "")).strip()[:100],
        "button_command": str(post.get("button_command", "")).strip(),
        "button_icon": icon[:40],
        "button_style": style if style in STYLES else "btn-default",
        "button_size": size if size in SIZES else "btn-md",
        "button_direct": 1 if post.get("button_direct") else 0,
    }


def submit(settings: Settings, post: dict) -> tuple[bool, str, dict | None]:
    buttons = load(settings)
    if buttons is None:
        return False, "Could not read buttons file", None
    data = sanitize(post)
    if not data["button_title"] or not data["button_command"]:
        return False, "Button title and command are required", None

    raw_id = str(post.get("button_id", "")).strip()
    if raw_id != "" and raw_id.isdigit():
        index = int(raw_id)
        if index >= len(buttons):
            return False, "Button not found", None
        data["button_hash"] = buttons[index].get("button_hash") or secrets.token_hex(16)
        buttons[index] = data
        message = "Button updated"
    else:
        data["button_hash"] = secrets.token_hex(16)
        buttons.append(data)
        message = "Button created"

    error = save(settings, buttons)
    return (error == "", error or message, data)


def delete(settings: Settings, button_id: str) -> tuple[bool, str]:
    buttons = load(settings)
    if buttons is None:
        return False, "Could not read buttons file"
    if not button_id.isdigit() or int(button_id) >= len(buttons):
        return False, "Button not found"
    buttons.pop(int(button_id))
    error = save(settings, buttons)
    return (error == "", error or "Button deleted")


def reorder(settings: Settings, order: list[str]) -> tuple[bool, str]:
    buttons = load(settings)
    if buttons is None:
        return False, "Could not read buttons file"
    reordered = []
    used: set[int] = set()
    for raw in order:
        try:
            index = int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(buttons) and index not in used:
            reordered.append(buttons[index])
            used.add(index)
    for index, button in enumerate(buttons):
        if index not in used:
            reordered.append(button)
    error = save(settings, reordered)
    return (error == "", error or "Buttons reordered")


def regenerate_hash(settings: Settings, button_id: str) -> tuple[bool, str, str]:
    buttons = load(settings)
    if buttons is None:
        return False, "Could not read buttons file", ""
    if not button_id.isdigit() or int(button_id) >= len(buttons):
        return False, "Button not found", ""
    new_hash = secrets.token_hex(16)
    buttons[int(button_id)]["button_hash"] = new_hash
    error = save(settings, buttons)
    return (error == "", error or "Hash regenerated", new_hash)


def find_by_hash(settings: Settings, button_hash: str) -> dict | None:
    buttons = load(settings)
    if not buttons:
        return None
    for button in buttons:
        stored = str(button.get("button_hash", ""))
        if stored and hmac.compare_digest(stored, button_hash):
            return button
    return None


def execute(settings: Settings, executor: Executor, button: dict) -> dict[str, object]:
    command = str(button.get("button_command", "")).strip()
    if not command:
        return {"ok": False, "error": "Button has no command configured"}
    result = executor.run(command, timeout=120)
    if result.ok:
        return {"ok": True, "output": result.output}
    return {"ok": False, "error": result.output or "Command failed"}


def api_log(settings: Settings, entry: dict) -> None:
    path = settings.command_logs_dir / "api_calls.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
        if path.stat().st_size > 500_000:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > 1000:
                path.write_text("\n".join(lines[-1000:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def hash_preview(button_hash: str) -> str:
    return (button_hash[:8] + "…") if button_hash else ""


def api_log_entries(settings: Settings, limit: int = 50) -> list[dict]:
    """Recent API calls without ever exposing a usable key."""
    path = settings.command_logs_dir / "api_calls.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    entries = []
    for line in reversed(lines[-200:]):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        entry.pop("hash", None)
        entry["time"] = entry.get("time") or time.strftime(
            "%Y-%m-%d %H:%M:%S", time.localtime(int(entry.get("ts", 0)))
        )
        entries.append(entry)
        if len(entries) >= limit:
            break
    return entries


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
