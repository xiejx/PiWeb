from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import __version__
from .config import BASE_DIR, STATIC_DIR, TEMPLATES_DIR, ModuleConfig, Settings
from .i18n import SUPPORTED_LANGS, current_lang, translator
from .security import (
    auth_configured,
    csrf_token,
    default_credentials,
    open_mode,
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@dataclass
class NavGroup:
    name: str
    items: list[ModuleConfig] = field(default_factory=list)


def module_href(module: ModuleConfig) -> str:
    if module.url:
        return module.url
    if module.key == "backup":
        return "/backup"
    if module.key == "check":
        return "/check"
    return f"/{module.key}"


def build_nav(settings: Settings) -> tuple[list[ModuleConfig], list[NavGroup]]:
    inline: list[ModuleConfig] = []
    groups: dict[str, NavGroup] = {}
    for module in settings.modules.values():
        if not module.enabled or module.no_nav:
            continue
        if module.group:
            groups.setdefault(module.group, NavGroup(module.group)).items.append(module)
        else:
            inline.append(module)

    order = _load_menu_order(settings)
    if order:
        rank = {key: index for index, key in enumerate(order)}
        inline.sort(key=lambda m: rank.get(m.key, len(rank)))
    return inline, list(groups.values())


def _load_menu_order(settings: Settings) -> list[str]:
    for path in (settings.data_dir / "menu_order.json", BASE_DIR / "include" / "menu_order.json"):
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, list):
            return [str(item) for item in data]
    return []


def _bar_color(percent: float) -> str:
    return "danger" if percent >= 90 else "warning" if percent >= 70 else "success"


def _temp_color(temp: float) -> str:
    return "danger" if temp >= 80 else "warning" if temp >= 70 else "success"


def _service_class(state: str) -> str:
    return {
        "active": "badge-soft-success",
        "failed": "badge-soft-danger",
        "inactive": "badge-soft-muted",
    }.get(state, "badge-soft-warning")


def _bi_icon(name: str) -> str:
    """Button icons are stored as Bootstrap Icons names; pass them through."""
    cleaned = re.sub(r"[^a-z0-9\-]", "", (name or "").strip().lower())
    return cleaned or "lightning-charge"


def _bs_style(style: str) -> str:
    return {
        "btn-default": "btn-secondary",
        "btn-primary": "btn-primary",
        "btn-success": "btn-success",
        "btn-info": "btn-info",
        "btn-warning": "btn-warning",
        "btn-danger": "btn-danger",
    }.get(style, "btn-secondary")


def _bs_size(size: str) -> str:
    return {"btn-xs": "btn-sm", "btn-sm": "btn-sm", "btn-lg": "btn-lg"}.get(size, "")


def _filesize(num: float) -> str:
    from .executor import format_bytes

    return format_bytes(float(num or 0))


templates.env.filters.update(
    {
        "bar_color": _bar_color,
        "temp_color": _temp_color,
        "service_class": _service_class,
        "bi_icon": _bi_icon,
        "bs_style": _bs_style,
        "bs_size": _bs_size,
        "filesize": _filesize,
    }
)


@lru_cache(maxsize=1)
def _asset_version() -> str:
    latest = 0.0
    for path in STATIC_DIR.rglob("*"):
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    return str(int(latest) or int(time.time()))


def render(request: Request, template: str, status_code: int = 200, **context):
    settings: Settings = request.app.state.settings
    lang = current_lang(request, settings)
    inline, groups = build_nav(settings)
    base = {
        "request": request,
        "settings": settings,
        "t": translator(lang),
        "lang": lang,
        "langs": SUPPORTED_LANGS,
        "csrf_token": csrf_token(request),
        "nav_inline": inline,
        "nav_groups": groups,
        "module_href": module_href,
        "asset_version": _asset_version(),
        "app_version": __version__,
        "current_path": request.url.path,
        "auth_configured": auth_configured(settings),
        "open_mode": open_mode(settings),
        "default_credentials": default_credentials(settings),
        "flashes": pop_flashes(request),
    }
    base.update(context)
    return templates.TemplateResponse(
        request, template, base, status_code=status_code
    )


def flash(request: Request, message: str, level: str = "success") -> None:
    request.session.setdefault("flash", []).append({"message": message, "level": level})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return request.session.pop("flash", [])


def render_string(request: Request, template: str, **context) -> str:
    """Render a fragment to a string (used by HTMX partial responses)."""
    response = render(request, template, **context)
    return response.body.decode("utf-8")


def nav_order_file(settings: Settings) -> Path:
    return settings.data_dir / "menu_order.json"
