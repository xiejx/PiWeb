from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any

from .config import Settings
from .executor import Executor
from .system import cpu_temperature

SERIAL_RE = re.compile(r"^(28-[0-9a-f]+)$", re.IGNORECASE)
NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")
DHT_CHIPS = {"DHT11", "DHT22", "AM2302"}


def _settings_section(settings: Settings) -> dict[str, Any]:
    section = settings.raw_config.get("sensors", {})
    return section if isinstance(section, dict) else {}


def list_sensors(settings: Settings, executor: Executor) -> list[dict[str, Any]]:
    """Discover local probes: SoC, 1-Wire DS18B20 and configured DHT/custom ones."""
    items: list[dict[str, Any]] = [
        {"id": "soc", "name": "SoC temperature", "kind": "soc", "unit": "°C"}
    ]

    result = executor.run(
        "ls -1d /sys/bus/w1/devices/28-*/w1_slave 2>/dev/null", timeout=8
    )
    seen: set[str] = set()
    for raw_path in result.stdout.splitlines():
        path = raw_path.strip()
        if not path:
            continue
        serial = Path(path).parent.name
        if not SERIAL_RE.match(serial) or serial in seen:
            continue
        seen.add(serial)
        items.append(
            {
                "id": f"w1:{serial}",
                "name": f"DS18B20 {serial}",
                "kind": "onewire",
                "path": path,
                "unit": "°C",
            }
        )

    section = _settings_section(settings)
    dht_entries = section.get("dht", [])
    if isinstance(dht_entries, list):
        for entry in dht_entries:
            if not isinstance(entry, dict) or entry.get("pin") is None:
                continue
            chip = str(entry.get("chip", entry.get("type", "DHT11"))).upper()
            if chip not in DHT_CHIPS:
                chip = "DHT11"
            try:
                pin = int(entry["pin"])
            except (TypeError, ValueError):
                continue
            if not 0 <= pin <= 40:
                continue
            items.append(
                {
                    "id": f"dht:{chip.lower()}:{pin}",
                    "name": str(entry.get("name", f"{chip} GPIO{pin}")),
                    "kind": "dht",
                    "chip": chip,
                    "pin": pin,
                    "unit": "°C",
                }
            )

    custom_entries = section.get("custom", [])
    if isinstance(custom_entries, list):
        for index, entry in enumerate(custom_entries):
            if not isinstance(entry, dict):
                continue
            command = str(entry.get("cmd", "")).strip()
            if not command:
                continue
            items.append(
                {
                    "id": f"cmd:{index}",
                    "name": str(entry.get("name", f"Custom sensor {index + 1}")),
                    "kind": "command",
                    "cmd": command,
                    "unit": str(entry.get("unit", "")),
                }
            )

    return items


def _parse_number(text: str) -> float | None:
    match = NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _read_one(executor: Executor, sensor: dict[str, Any]) -> dict[str, Any]:
    kind = sensor["kind"]
    entry = dict(sensor)
    entry.update({"value": None, "display": "", "error": ""})

    if kind == "soc":
        temperature = cpu_temperature(executor)
        if temperature is None:
            entry["error"] = "Temperature is not available on this system."
        else:
            entry["value"] = round(float(temperature), 1)
            entry["display"] = f"{entry['value']:.1f} °C"
        return entry

    if kind == "onewire":
        result = executor.run(
            f"cat {shlex.quote(str(sensor['path']))} 2>&1", timeout=8
        )
        if not result.ok:
            entry["error"] = result.output or "Could not read the sensor."
            return entry
        if "crc=" in result.stdout.lower() and "YES" not in result.stdout.upper():
            entry["error"] = "CRC check failed — check the wiring."
            return entry
        match = re.search(r"t=(-?\d+)", result.stdout)
        if not match:
            entry["error"] = "The sensor returned no temperature value."
            return entry
        value = int(match.group(1)) / 1000.0
        if value <= -100 or value >= 125:
            entry["error"] = "The sensor reported an invalid temperature."
            return entry
        entry["value"] = round(value, 2)
        entry["display"] = f"{entry['value']:.2f} °C"
        return entry

    if kind == "dht":
        chip = sensor["chip"]
        script = (
            "import json\n"
            "try:\n"
            "    import adafruit_dht, board\n"
            f"    sensor = adafruit_dht.{chip}(board.D{int(sensor['pin'])})\n"
            "    try:\n"
            "        print(json.dumps({'temperature': sensor.temperature,"
            " 'humidity': sensor.humidity}))\n"
            "    finally:\n"
            "        sensor.exit()\n"
            "except Exception as exc:\n"
            "    print(json.dumps({'error': str(exc)}))\n"
        )
        result = executor.run(
            f"python3 -c {shlex.quote(script)} 2>&1", timeout=20
        )
        payload: dict[str, Any] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except ValueError:
                    payload = {}
        if payload.get("error"):
            entry["error"] = str(payload["error"])
            return entry
        temperature = payload.get("temperature")
        humidity = payload.get("humidity")
        if temperature is None:
            entry["error"] = result.output or "The sensor returned no data."
            return entry
        entry["value"] = round(float(temperature), 1)
        entry["display"] = f"{entry['value']:.1f} °C"
        if humidity is not None:
            entry["display"] += f" · {float(humidity):.0f} %RH"
        return entry

    if kind == "command":
        result = executor.run(str(sensor["cmd"]), timeout=20)
        if not result.ok:
            entry["error"] = result.output or "The sensor command failed."
            return entry
        payload = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    payload = json.loads(line)
                except ValueError:
                    payload = {}
        value = None
        unit = sensor.get("unit", "")
        if isinstance(payload, dict) and payload.get("value") is not None:
            value = _parse_number(str(payload["value"]))
            unit = str(payload.get("unit", unit))
        else:
            value = _parse_number(result.stdout)
        if value is None:
            entry["error"] = result.output or "The command returned no numeric value."
            return entry
        entry["value"] = value
        entry["display"] = f"{value:g} {unit}".strip()
        return entry

    entry["error"] = "Unsupported sensor type."
    return entry


def read_sensor(
    settings: Settings, executor: Executor, sensor_id: str
) -> dict[str, Any]:
    for sensor in list_sensors(settings, executor):
        if sensor["id"] == sensor_id:
            return _read_one(executor, sensor)
    raise ValueError("Unknown sensor.")


def read_all(settings: Settings, executor: Executor) -> list[dict[str, Any]]:
    return [
        _read_one(executor, sensor) for sensor in list_sensors(settings, executor)
    ]
