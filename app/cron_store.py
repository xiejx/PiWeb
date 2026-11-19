from __future__ import annotations

import re
from pathlib import Path

from .config import BASE_DIR
from .executor import Executor

KEYWORDS = {
    "@reboot", "@yearly", "@annually", "@monthly", "@weekly",
    "@daily", "@midnight", "@hourly",
}
MONTHS = {name: index for index, name in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
)}
DOWS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _token_value(token: str, minimum: int, maximum: int, names: dict[str, int] | None):
    token = token.lower()
    if names and token in names:
        value = names[token]
    elif token.isdigit():
        value = int(token)
    else:
        return None
    return value if minimum <= value <= maximum else None


def _field_valid(field: str, minimum: int, maximum: int, names=None) -> bool:
    if not field:
        return False
    for item in field.split(","):
        if not item:
            return False
        if "/" in item:
            bits = item.split("/")
            if len(bits) != 2 or not bits[1].isdigit() or int(bits[1]) < 1:
                return False
            item = bits[0]
        if item == "*":
            continue
        if "-" in item:
            parts = item.split("-")
            if len(parts) != 2:
                return False
            low = _token_value(parts[0], minimum, maximum, names)
            high = _token_value(parts[1], minimum, maximum, names)
            if low is None or high is None or low > high:
                return False
        elif _token_value(item, minimum, maximum, names) is None:
            return False
    return True


def validate_schedule(expr: str) -> bool:
    expr = (expr or "").strip()
    if not expr:
        return False
    if expr.startswith("@"):
        return expr.lower() in KEYWORDS
    parts = expr.split()
    if len(parts) != 5:
        return False
    return (
        _field_valid(parts[0], 0, 59)
        and _field_valid(parts[1], 0, 23)
        and _field_valid(parts[2], 1, 31)
        and _field_valid(parts[3], 1, 12, MONTHS)
        and _field_valid(parts[4], 0, 7, DOWS)
    )


def read_user_crontab(executor: Executor) -> str:
    result = executor.run("crontab -l 2>/dev/null", timeout=15)
    return result.stdout


def _write_user_crontab(executor: Executor, text: str) -> tuple[bool, str]:
    result = executor.run("crontab -", timeout=15, input=text)
    if result.ok:
        return True, ""
    return False, result.output or "crontab update failed"


def read_system_crontab() -> str:
    for path in (Path("/etc/crontab"),):
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def add_job(executor: Executor, schedule: str, command: str) -> tuple[bool, str]:
    schedule = schedule.strip()
    command = command.strip()
    if not validate_schedule(schedule):
        return False, "Invalid cron schedule expression."
    if not command or "\n" in command or "\r" in command:
        return False, "Command must be a single line."
    if len(command) > 1000:
        return False, "Command is too long (max 1000 characters)."

    current = read_user_crontab(executor)
    line = f"{schedule} {command}"
    if line in current:
        return False, "That exact cron job already exists."
    text = current
    if text and not text.endswith("\n"):
        text += "\n"
    text += line + "\n"
    ok, error = _write_user_crontab(executor, text)
    return (True, "Cron job added.") if ok else (False, error)


def delete_job(executor: Executor, index: int) -> tuple[bool, str]:
    current = read_user_crontab(executor)
    lines = current.splitlines()
    if index < 0 or index >= len(lines) or not lines[index].strip():
        return False, "Cron job not found."
    del lines[index]
    text = "\n".join(lines)
    if text:
        text += "\n"
    ok, error = _write_user_crontab(executor, text)
    return (True, "Cron job removed.") if ok else (False, error)


def scripts_with_schedules(executor: Executor, script_names: list[str]) -> dict[str, str]:
    current = read_user_crontab(executor)
    found: dict[str, str] = {}
    for name in script_names:
        # Accept the legacy "# gumcp-script:" marker so pre-rename schedules stay
        # visible; new/updated entries always use the PiWeb marker.
        markers = (f"# piweb-script:{name}", f"# gumcp-script:{name}")
        for line in current.splitlines():
            for marker in markers:
                if line.rstrip().endswith(marker):
                    found[name] = line[: -len(f" {marker}")].strip()
                    break
            if name in found:
                break
    return found


def schedule_script(
    executor: Executor, name: str, schedule: str, interpreter: str
) -> tuple[bool, str]:
    if not validate_schedule(schedule):
        return False, "Invalid cron schedule expression."
    marker = f"# piweb-script:{name}"
    legacy_marker = f"# gumcp-script:{name}"
    script_path = BASE_DIR / "user_scripts" / name
    current = read_user_crontab(executor)
    kept = [
        line
        for line in current.splitlines()
        if not line.rstrip().endswith((marker, legacy_marker))
    ]
    kept.append(f"{schedule} {interpreter} {script_path} {marker}")
    text = "\n".join(kept) + "\n"
    ok, error = _write_user_crontab(executor, text)
    return (True, f"Scheduled {name}.") if ok else (False, error)


def unschedule_script(executor: Executor, name: str) -> tuple[bool, str]:
    marker = f"# piweb-script:{name}"
    legacy_marker = f"# gumcp-script:{name}"
    current = read_user_crontab(executor)
    kept = [
        line
        for line in current.splitlines()
        if not line.rstrip().endswith((marker, legacy_marker))
    ]
    if len(kept) == len(current.splitlines()):
        return False, "This script has no schedule."
    ok, error = _write_user_crontab(executor, "\n".join(kept) + "\n")
    return (True, "Schedule removed.") if ok else (False, error)


def describe(expr: str) -> str:
    expr = (expr or "").strip()
    keywords = {
        "@reboot": "at every system boot",
        "@hourly": "every hour, on the hour",
        "@daily": "every day at midnight",
        "@midnight": "every day at midnight",
        "@weekly": "every week (Sunday at midnight)",
        "@monthly": "every month (1st at midnight)",
        "@yearly": "every year (Jan 1 at midnight)",
        "@annually": "every year (Jan 1 at midnight)",
    }
    if expr in keywords:
        return keywords[expr]
    parts = expr.split()
    if len(parts) < 5:
        return ""
    minute, hour, dom, month, dow = parts[:5]
    all_rest = dom == "*" and month == "*" and dow == "*"
    if minute == "*" and hour == "*" and all_rest:
        return "every minute"
    every = re.fullmatch(r"\*/(\d+)", minute)
    if every and hour == "*" and all_rest:
        return f"every {every.group(1)} minutes"
    if minute.isdigit() and hour == "*" and all_rest:
        return f"at minute {minute} of every hour"
    if minute.isdigit() and hour.isdigit():
        when = f"at {int(hour):02d}:{int(minute):02d}"
        days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
        if all_rest:
            return when + " every day"
        if dom == "*" and month == "*" and dow.isdigit() and 0 <= int(dow) <= 6:
            return when + " every " + days[int(dow)]
        if dom.isdigit() and month == "*" and dow == "*":
            return when + " on day " + dom + " of every month"
    return ""
