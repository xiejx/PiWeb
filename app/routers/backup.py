from __future__ import annotations

import base64
import binascii
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from ..config import BASE_DIR, Settings
from ..config import tomllib as _toml
from ..security import auth_log, client_ip
from ..templating import flash, render

router = APIRouter()

MAX_BUNDLE_BYTES = 8 * 1024 * 1024
SCRIPT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(sh|py|txt)$")
FIXED_ENTRIES = (
    "config.toml",
    "data/menu_order.json",
    "data/links.json",
    "buttons/buttons.json",
    "user_scripts/.pins.json",
)


def allowed_entry(rel: str) -> bool:
    """Only these relative paths may appear in a bundle."""
    if rel in FIXED_ENTRIES:
        return True
    if rel.startswith("user_scripts/"):
        name = rel[len("user_scripts/"):]
        return len(name) <= 64 and bool(SCRIPT_RE.match(name))
    return False


def target_path(settings: Settings, rel: str) -> Path | None:
    """Map a whitelisted relative path to the real file, never trusting the bundle."""
    if rel == "config.toml":
        return settings.config_path or BASE_DIR / "config.toml"
    if rel == "data/menu_order.json":
        return settings.data_dir / "menu_order.json"
    if rel == "data/links.json":
        return settings.data_dir / "links.json"
    if rel == "buttons/buttons.json":
        return settings.buttons_dir / "buttons.json"
    if rel == "user_scripts/.pins.json":
        return settings.scripts_dir / ".pins.json"
    if rel.startswith("user_scripts/"):
        return settings.scripts_dir / rel[len("user_scripts/"):]
    return None


def collect(settings: Settings) -> dict[str, str]:
    files: dict[str, str] = {}
    candidates = list(FIXED_ENTRIES)
    if settings.scripts_dir.is_dir():
        for path in settings.scripts_dir.iterdir():
            rel = f"user_scripts/{path.name}"
            if allowed_entry(rel):
                candidates.append(rel)

    for rel in candidates:
        if not allowed_entry(rel):
            continue
        path = target_path(settings, rel)
        if path is None or path.is_symlink() or not path.is_file():
            continue
        try:
            files[rel] = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
    return files


@router.get("/backup", response_class=HTMLResponse)
def backup_page(request: Request):
    return render(request, "backup.html")


@router.post("/backup/download")
def backup_download(request: Request):
    settings = request.app.state.settings
    now = datetime.now().astimezone()
    bundle = {
        "piweb_backup": 1,
        "created": now.isoformat(timespec="seconds"),
        "files": collect(settings),
    }
    auth_log(
        settings,
        "backup",
        "backup downloaded",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    filename = f"piweb-backup-{now.strftime('%Y-%m-%d')}.json"
    return JSONResponse(
        bundle,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/backup/restore")
async def backup_restore(
    request: Request,
    bundle: UploadFile = File(...),
    restore_config: str = Form(""),
):
    settings = request.app.state.settings
    raw = await bundle.read()

    if not bundle.filename:
        flash(request, "No backup file was uploaded.", "danger")
        return RedirectResponse("/backup", status_code=303)
    if len(raw) > MAX_BUNDLE_BYTES:
        flash(request, "Backup file is too large (max 8 MB).", "danger")
        return RedirectResponse("/backup", status_code=303)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        flash(request, "That file is not a PiWeb backup (invalid JSON).", "danger")
        return RedirectResponse("/backup", status_code=303)

    is_backup = (
        isinstance(payload, dict)
        and bool(payload.get("piweb_backup"))
        and isinstance(payload.get("files"), dict)
    )
    if not is_backup:
        flash(request, "That file is not a PiWeb backup.", "danger")
        return RedirectResponse("/backup", status_code=303)

    with_config = restore_config.strip().lower() in {"1", "true", "on", "yes"}
    restored = 0
    skipped: list[str] = []

    for rel, encoded in payload["files"].items():
        rel = str(rel)
        # Never trust the bundle: an uploaded path is exactly what a traversal
        # attack looks like, so validate it against the whitelist first.
        if not allowed_entry(rel):
            skipped.append(f"{rel} (not an allowed path)")
            continue
        try:
            body = base64.b64decode(str(encoded), validate=True)
        except (binascii.Error, ValueError):
            skipped.append(f"{rel} (corrupt)")
            continue

        if rel == "config.toml":
            if not with_config:
                skipped.append("config.toml (tick the box to restore it)")
                continue
            try:
                _toml.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                skipped.append("config.toml (not valid TOML)")
                continue

        path = target_path(settings, rel)
        if path is None:
            skipped.append(f"{rel} (not an allowed path)")
            continue
        if path.is_symlink():
            skipped.append(f"{rel} (target is a symlink)")
            continue

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if rel == "config.toml" and path.is_file():
                shutil.copy2(path, path.with_name(path.name + ".restore.bak"))
            path.write_bytes(body)
        except OSError:
            skipped.append(f"{rel} (not writable)")
            continue
        restored += 1

    auth_log(
        settings,
        "restore",
        f"{restored} file(s) restored"
        + (", including config.toml" if with_config else ""),
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    flash(
        request,
        f"{restored} file(s) restored.",
        "success" if restored else "warning",
    )
    for item in skipped:
        flash(request, f"Skipped: {item}", "warning")
    return RedirectResponse("/backup", status_code=303)
