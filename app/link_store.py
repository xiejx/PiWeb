from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

from .config import Settings

MAX_LINKS = 500
MAX_URL_LEN = 2000
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def links_file(settings: Settings) -> Path:
    return settings.data_dir / "links.json"


def load(settings: Settings) -> list[dict] | None:
    path = links_file(settings)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [row for row in data if isinstance(row, dict)]


def save(settings: Settings, links: list[dict]) -> str:
    path = links_file(settings)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(links, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError:
        return (
            f"Cannot write {path} — fix with: sudo chown -R "
            f"{settings.ssh_user}:{settings.ssh_user} {path.parent}"
        )
    return ""


def normalize_url(raw: object) -> str:
    """Return a safe http(s) URL, or "" for anything else.

    Rejecting javascript:/data:/file: here is the security boundary: Jinja2
    escaping alone does not stop a javascript: href from running on click.
    """
    value = str(raw or "").strip()
    if not value:
        return ""
    # If the input already carries a scheme (javascript:, data:, file:, …),
    # parse it as-is so the whitelist below can reject it. Only prepend https
    # when there is no scheme at all.
    if not SCHEME_RE.match(value):
        value = "https://" + value.lstrip("/")
    try:
        parsed = urlparse(value)
    except ValueError:
        return ""
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return ""
    return value[:MAX_URL_LEN]


def sanitize(post: dict, existing: dict | None = None) -> dict:
    icon = str(post.get("icon", "")).strip()[:8]
    hits = 0
    if existing is not None:
        try:
            hits = max(0, int(existing.get("hits", 0)))
        except (TypeError, ValueError):
            hits = 0
    return {
        "title": str(post.get("title", "")).strip()[:100],
        "url": normalize_url(post.get("url", "")),
        "group": str(post.get("group", "")).strip()[:60],
        "icon": icon,
        "hits": hits,
    }


def submit(settings: Settings, post: dict) -> tuple[bool, str, dict | None]:
    links = load(settings)
    if links is None:
        return False, "Could not read links file", None

    raw_id = str(post.get("link_id", "")).strip()
    index = -1
    if raw_id.isdigit():
        index = int(raw_id)
        if index >= len(links):
            return False, "Link not found", None

    data = sanitize(post, links[index] if index >= 0 else None)
    if not data["title"] or not data["url"]:
        return False, "Link title and a valid http(s) URL are required", None
    if index < 0 and len(links) >= MAX_LINKS:
        return False, f"Too many links (limit {MAX_LINKS})", None

    if index >= 0:
        links[index] = data
        message = "Link updated"
    else:
        links.append(data)
        message = "Link added"

    error = save(settings, links)
    if error:
        return False, error, None
    return True, message, data


def delete(settings: Settings, raw_id: object) -> tuple[bool, str]:
    links = load(settings)
    if links is None:
        return False, "Could not read links file"
    raw = str(raw_id or "").strip()
    if not raw.isdigit() or int(raw) >= len(links):
        return False, "Link not found"
    links.pop(int(raw))
    error = save(settings, links)
    if error:
        return False, error
    return True, "Link deleted"
