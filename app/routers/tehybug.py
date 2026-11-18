from __future__ import annotations

import json
import math
import sqlite3
import time
from contextlib import contextmanager
from datetime import date as date_cls, datetime
from typing import Iterator

from fastapi import APIRouter, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)

from ..templating import flash, render

router = APIRouter()
# Devices post readings without a session, so this router must be mounted
# without the require_csrf dependency (the auth gate already allows the path).
public_router = APIRouter()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tehybugs (
    bug_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_key         CHAR(50),
    bug_title       CHAR(50),
    bug_description TEXT,
    bug_details     TEXT,
    bug_type        CHAR(10),
    bug_settings    TEXT,
    bug_position    INTEGER,
    bug_last_active INTEGER,
    bug_time        INTEGER
);
CREATE TABLE IF NOT EXISTS tehybug_data (
    data_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    data_bug_id  INTEGER,
    data_bug_key CHAR(50),
    data_details TEXT,
    data_time    INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tehybugs_bug_key ON tehybugs (bug_key);
CREATE INDEX IF NOT EXISTS idx_tehybug_data_bug_time ON tehybug_data (data_bug_id, data_time);
CREATE INDEX IF NOT EXISTS idx_tehybug_data_bug_key ON tehybug_data (data_bug_key);
"""

TRACK_FIELDS = ("bug_key", "t", "h", "p", "chipid", "sensor")


@contextmanager
def _connect(settings) -> Iterator[sqlite3.Connection]:
    path = settings.data_dir / "tehybug.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=3)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _load_details(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _to_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _format_ts(timestamp) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _display_title(bug: dict) -> str:
    return str(bug.get("bug_title") or bug.get("bug_key") or f"TeHyBug {bug.get('bug_id')}")


def _temp_icon(value: float | None) -> str:
    if value is None:
        return ""
    if value < 18:
        return "temperature_low"
    if value > 24:
        return "temperature_high"
    return "temperature_middle"


def _humidity_icon(value: float | None) -> str:
    if value is None:
        return ""
    if value < 30:
        return "humidity_low"
    if value > 50:
        return "humidity_high"
    return "humidity_middle"


def _load_bugs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM tehybugs ORDER BY bug_position ASC LIMIT 100"
    ).fetchall()
    bugs = []
    for row in rows:
        bug = dict(row)
        bug["bug_settings"] = _load_details(bug.get("bug_settings"))
        bug["system"] = str(bug["bug_settings"].get("system") or "metric")
        bugs.append(bug)
    return bugs


def _latest_readings(conn: sqlite3.Connection, bugs: list[dict]) -> dict[int, dict]:
    latest: dict[int, dict] = {}
    for bug in bugs:
        row = conn.execute(
            "SELECT data_details, data_time FROM tehybug_data "
            "WHERE data_bug_id = ? ORDER BY data_time DESC, data_id DESC LIMIT 1",
            (bug["bug_id"],),
        ).fetchone()
        if row is None:
            continue
        details = _load_details(row["data_details"])
        if not details:
            continue
        latest[bug["bug_id"]] = {
            "details": details,
            "time_text": _format_ts(row["data_time"]),
        }
    return latest


def _reading_rows(bugs: list[dict], latest: dict[int, dict]) -> list[dict]:
    rows = []
    for bug in bugs:
        entry = latest.get(bug["bug_id"])
        if not entry:
            continue
        details = entry["details"]
        imperial = bug["system"] == "imperial"
        temp = _to_float(details.get("t"))
        humi = _to_float(details.get("h"))
        press = _to_float(details.get("p"))
        if imperial:
            if temp is not None:
                temp = round(temp * 1.8 + 32, 2)
            if press is not None:
                press = round(press / 33.86389, 2)
        rows.append(
            {
                "bug_id": bug["bug_id"],
                "title": _display_title(bug),
                "time": entry["time_text"],
                "temp": temp,
                "humi": humi,
                "press": press,
                "temp_icon": _temp_icon(temp),
                "humi_icon": _humidity_icon(humi),
                "temp_unit": "°F" if imperial else "°C",
                "press_unit": "inHg" if imperial else "hPa",
            }
        )
    return rows


def _parse_day(value: str) -> tuple[int, int] | None:
    parts = value.split("-")
    if len(parts) != 3:
        return None
    try:
        year, month, day = (int(part) for part in parts)
        start = int(datetime(year, month, day).timestamp())
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return start, start + 86400


@router.get("/modules/tehybug", response_class=HTMLResponse)
def tehybug_page(request: Request):
    settings = request.app.state.settings
    with _connect(settings) as conn:
        bugs = _load_bugs(conn)
        latest = _latest_readings(conn, bugs)
    return render(
        request,
        "tehybug.html",
        bugs=bugs,
        readings=_reading_rows(bugs, latest),
        bug_ids=[bug["bug_id"] for bug in bugs],
        today=date_cls.today().isoformat(),
        track_url=str(request.base_url).rstrip("/") + "/modules/tehybug/track",
    )


@router.post("/modules/tehybug/save")
async def tehybug_save(request: Request):
    settings = request.app.state.settings
    form = await request.form()
    try:
        bug_id = int(str(form.get("bug_id", "0") or 0))
    except (TypeError, ValueError):
        bug_id = 0
    key = str(form.get("bug_key", "") or "").strip()
    title = str(form.get("bug_title", "") or "").strip()
    description = str(form.get("bug_description", "") or "").strip()
    system = "imperial" if str(form.get("bug_settings_system", "")) == "imperial" else "metric"
    now = int(time.time())

    if bug_id > 0:
        with _connect(settings) as conn:
            row = conn.execute(
                "SELECT bug_settings FROM tehybugs WHERE bug_id = ? LIMIT 1",
                (bug_id,),
            ).fetchone()
            if row is None:
                flash(request, "That TeHyBug no longer exists.", "danger")
                return RedirectResponse("/modules/tehybug", status_code=303)
            stored = _load_details(row["bug_settings"])
            stored["system"] = system
            conn.execute(
                "UPDATE tehybugs SET bug_title = ?, bug_description = ?, "
                "bug_settings = ?, bug_time = ? WHERE bug_id = ?",
                (title, description, json.dumps(stored), now, bug_id),
            )
        flash(request, "TeHyBug settings saved.")
        return RedirectResponse("/modules/tehybug", status_code=303)

    if not key:
        flash(request, "A TeHyBug key is required.", "danger")
        return RedirectResponse("/modules/tehybug", status_code=303)

    with _connect(settings) as conn:
        exists = conn.execute(
            "SELECT bug_id FROM tehybugs WHERE bug_key = ? LIMIT 1", (key,)
        ).fetchone()
        if exists is not None:
            flash(request, "That TeHyBug key already exists.", "danger")
            return RedirectResponse("/modules/tehybug", status_code=303)
        cursor = conn.execute(
            "INSERT INTO tehybugs (bug_key, bug_title, bug_description, bug_settings, bug_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (key, title, description, json.dumps({"system": system}), now),
        )
        new_id = cursor.lastrowid
        # Readings may arrive before the bug is registered.
        conn.execute(
            "UPDATE tehybug_data SET data_bug_id = ? WHERE data_bug_key = ?",
            (new_id, key),
        )
    flash(request, "TeHyBug added.")
    return RedirectResponse("/modules/tehybug", status_code=303)


@router.post("/modules/tehybug/delete")
async def tehybug_delete(request: Request):
    settings = request.app.state.settings
    form = await request.form()
    try:
        bug_id = int(str(form.get("bug_id", "0") or 0))
    except (TypeError, ValueError):
        bug_id = 0
    if bug_id <= 0:
        flash(request, "Invalid TeHyBug.", "danger")
        return RedirectResponse("/modules/tehybug", status_code=303)

    with _connect(settings) as conn:
        conn.execute("DELETE FROM tehybug_data WHERE data_bug_id = ?", (bug_id,))
        cursor = conn.execute("DELETE FROM tehybugs WHERE bug_id = ?", (bug_id,))
        deleted = cursor.rowcount > 0
    flash(
        request,
        "TeHyBug deleted." if deleted else "TeHyBug not found.",
        "success" if deleted else "warning",
    )
    return RedirectResponse("/modules/tehybug", status_code=303)


@router.get("/modules/tehybug/chart_data")
def tehybug_chart_data(
    request: Request,
    act: str = "",
    tehy_bug_id: int = 0,
    date: str = "",
):
    empty = [[], [], [], []]
    if act != "by_date" or tehy_bug_id <= 0:
        return JSONResponse(empty)
    window = _parse_day(date or date_cls.today().isoformat())
    if window is None:
        return JSONResponse(empty)
    start, end = window

    settings = request.app.state.settings
    with _connect(settings) as conn:
        rows = conn.execute(
            "SELECT data_details, data_time FROM tehybug_data "
            "WHERE data_bug_id = ? AND data_time BETWEEN ? AND ? "
            "ORDER BY data_time ASC",
            (tehy_bug_id, start, end),
        ).fetchall()

    times: list[str] = []
    temps: list[float | None] = []
    humis: list[float] = []
    press: list[float] = []

    for row in rows:
        details = _load_details(row["data_details"])
        if not details:
            continue
        times.append(
            datetime.fromtimestamp(int(row["data_time"])).strftime("%Y-%m-%d / %H:%M")
        )
        temps.append(_to_float(details.get("t")))
        humidity = _to_float(details.get("h"))
        if humidity is not None:
            humis.append(humidity)
        pressure = _to_float(details.get("p"))
        if pressure is not None:
            press.append(pressure)

    return JSONResponse(
        [times, temps, humis, press],
        headers={"Cache-Control": "max-age=300"},
    )


@public_router.get("/modules/tehybug/track")
@public_router.post("/modules/tehybug/track")
async def tehybug_track(request: Request):
    settings = request.app.state.settings
    headers = {"Cache-Control": "no-cache, must-revalidate"}

    provided = {key: str(value) for key, value in request.query_params.items()}
    if request.method == "POST":
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 - malformed body from a device
            form = {}
        for field in TRACK_FIELDS:
            value = form.get(field)
            if isinstance(value, str) and value and not provided.get(field):
                provided[field] = value

    key = (provided.get("bug_key") or provided.get("sensor") or "").strip()
    if not key:
        return PlainTextResponse("ERR: missing key", status_code=400, headers=headers)

    with _connect(settings) as conn:
        row = conn.execute(
            "SELECT bug_id, bug_key FROM tehybugs WHERE bug_key = ? LIMIT 1",
            (key,),
        ).fetchone()
        if row is None:
            return PlainTextResponse(
                "ERR: unknown key", status_code=404, headers=headers
            )

        # Store only the whitelisted sensor fields; never dump the raw request.
        details: dict[str, object] = {}
        for field in ("t", "h", "p"):
            number = _to_float(provided.get(field))
            if number is not None:
                details[field] = number
        for field in ("chipid", "sensor"):
            value = str(provided.get(field, "") or "").strip()
            if value:
                details[field] = value[:64]

        conn.execute(
            "INSERT INTO tehybug_data (data_bug_id, data_bug_key, data_details, data_time) "
            "VALUES (?, ?, ?, ?)",
            (
                int(row["bug_id"]),
                str(row["bug_key"] or ""),
                json.dumps(details),
                int(time.time()),
            ),
        )

    return PlainTextResponse("OK", headers=headers)
