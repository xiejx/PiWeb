from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings


def _post(url: str, data: bytes, headers: dict[str, str]) -> tuple[bool, str]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return 200 <= response.status < 300, ""
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, str(exc)


def configured(settings: Settings) -> bool:
    return bool(settings.alert_ntfy_url) or bool(
        settings.alert_telegram_token and settings.alert_telegram_chat
    )


def send_alert(settings: Settings, title: str, message: str) -> tuple[bool, str]:
    """Push a notification to every configured channel."""
    results: dict[str, bool] = {}
    body = message.encode("utf-8")

    if settings.alert_ntfy_url:
        ok, _ = _post(
            settings.alert_ntfy_url,
            body,
            {"Content-Type": "text/plain; charset=utf-8", "Title": title},
        )
        results["ntfy"] = ok

    if settings.alert_telegram_token and settings.alert_telegram_chat:
        url = (
            "https://api.telegram.org/bot"
            + urllib.parse.quote(settings.alert_telegram_token, safe="")
            + "/sendMessage"
        )
        data = urllib.parse.urlencode(
            {"chat_id": settings.alert_telegram_chat, "text": f"{title}\n{message}"}
        ).encode("utf-8")
        ok, _ = _post(url, data, {"Content-Type": "application/x-www-form-urlencoded"})
        results["telegram"] = ok

    if not results:
        return False, "No alert channel configured"

    failed = [name for name, ok in results.items() if not ok]
    if failed:
        return False, "Sending failed for: " + ", ".join(failed)
    return True, "sent via " + " and ".join(results)


def _state_path(settings: Settings):
    return settings.data_dir / "alerts_state.json"


def _load_state(settings: Settings) -> dict[str, int]:
    try:
        raw = json.loads(_state_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, int] = {}
    for key, value in raw.items():
        try:
            state[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return state


def alert_once(
    settings: Settings,
    key: str,
    title: str,
    message: str,
    ttl_seconds: int = 1800,
) -> bool:
    """Send an alert unless the same key fired within the cooldown window."""
    if not configured(settings):
        return False
    now = int(time.time())
    state = _load_state(settings)
    last = state.get(key, 0)
    if now - last < ttl_seconds:
        return False
    ok, _ = send_alert(settings, title, message)
    if ok:
        state[key] = now
        cutoff = now - 7 * 86400
        state = {k: v for k, v in state.items() if v >= cutoff}
        try:
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            _state_path(settings).write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        except OSError:
            pass
    return ok


def evaluate(settings: Settings, data: dict) -> None:
    """Threshold checks run whenever the dashboard collects metrics."""
    if not configured(settings):
        return
    temp = float(data.get("temp") or 0)
    if temp and temp >= settings.alert_temp_threshold:
        alert_once(
            settings,
            f"temp:{int(temp)}",
            "PiWeb: high temperature",
            f"CPU temperature is {temp:.1f} °C (threshold {settings.alert_temp_threshold} °C).",
        )

    disk = data.get("disk") or {}
    percent = float(disk.get("percent") or 0)
    if percent >= settings.alert_disk_threshold:
        alert_once(
            settings,
            f"disk:{int(percent)}",
            "PiWeb: disk almost full",
            f"Root filesystem is {percent:.0f}% full (threshold {settings.alert_disk_threshold}%).",
        )

    throttled = data.get("throttled") or {}
    flags = throttled.get("flags") or []
    if flags:
        alert_once(
            settings,
            "throttle:" + ",".join(flags),
            "PiWeb: power/thermal warning",
            "vcgencmd reports: " + ", ".join(flags),
        )
