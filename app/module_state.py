from __future__ import annotations

import json
from pathlib import Path

from .config import Settings


def state_file(settings: Settings) -> Path:
    return settings.data_dir / "modules_state.json"


def load_state(settings: Settings) -> dict[str, bool]:
    """Read the on/off overrides written by the Modules page."""
    try:
        raw = json.loads(state_file(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): bool(value) for key, value in raw.items()}


def apply(settings: Settings) -> None:
    """Apply persisted overrides on top of the configured defaults."""
    state = load_state(settings)
    for key, enabled in state.items():
        module = settings.modules.get(key)
        if module is not None:
            module.enabled = enabled


def set_enabled(settings: Settings, key: str, enabled: bool) -> None:
    module = settings.modules.get(key)
    if module is None:
        raise KeyError(key)
    state = load_state(settings)
    state[key] = enabled
    module.enabled = enabled
    path = state_file(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
