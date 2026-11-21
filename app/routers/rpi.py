from __future__ import annotations

import base64
import json
import os
import re
import shlex
import time
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..storage import list_disks, mount_device, mount_root, unmount_device
from ..system import boot_config_path, cpu_temperature, vcgencmd
from ..templating import render

router = APIRouter()

INTERFACES = {
    "i2c": "I2C",
    "spi": "SPI",
    "onewire": "1-Wire",
    "ssh": "SSH server",
    "vnc": "VNC server",
    "camera": "Camera (legacy)",
}

BOOT_FILES = {"config": "config.txt", "cmdline": "cmdline.txt"}


# ── vcgencmd metrics ─────────────────────────────────────────────────────────

def _clock(executor, name: str) -> str:
    match = re.search(r"=(\d+)", vcgencmd(executor, f"measure_clock {name}"))
    if not match:
        return "n/a"
    return f"{int(round(int(match.group(1)) / 1_000_000))} MHz"


def _voltage(executor, name: str) -> str:
    match = re.search(r"=([0-9.]+)V", vcgencmd(executor, f"measure_volts {name}"))
    return f"{match.group(1)} V" if match else "n/a"


def _memory(executor, name: str) -> str:
    match = re.search(r"=(\S+)", vcgencmd(executor, f"get_mem {name}"))
    return match.group(1) if match else "n/a"


def collect_metrics(executor) -> dict[str, object]:
    firmware = vcgencmd(executor, "version")
    codecs = {}
    for codec in ("H264", "H265", "MPG2", "WVC1"):
        output = vcgencmd(executor, f"codec_enabled {codec}").lower()
        codecs[codec] = "enabled" if "enabled" in output else "disabled"

    metrics = {
        "firmware": firmware or "n/a",
        "arm_clock": _clock(executor, "arm"),
        "core_clock": _clock(executor, "core"),
        "v3d_clock": _clock(executor, "v3d"),
        "core_volt": _voltage(executor, "core"),
        "sdram_volt": _voltage(executor, "sdram_c"),
        "mem_arm": _memory(executor, "arm"),
        "mem_gpu": _memory(executor, "gpu"),
        "codecs": codecs,
    }
    metrics["available"] = bool(firmware) or any(
        metrics[key] != "n/a"
        for key in (
            "arm_clock",
            "core_clock",
            "v3d_clock",
            "core_volt",
            "sdram_volt",
            "mem_arm",
            "mem_gpu",
        )
    )
    return metrics


# ── raspi-config interfaces ─────────────────────────────────────────────────

def interface_states(executor) -> dict[str, str]:
    states: dict[str, str] = {}
    for iface in INTERFACES:
        result = executor.run(
            f"sudo raspi-config nonint get_{iface} 2>/dev/null", timeout=10
        )
        value = result.output.strip() if result.ok else ""
        states[iface] = (
            "enabled" if value == "0" else "disabled" if value == "1" else "unknown"
        )
    return states


# ── Boot configuration files ────────────────────────────────────────────────

def _boot_root() -> Path | None:
    """Resolve /boot, or None where the platform has no POSIX /boot tree."""
    root = Path("/boot")
    if not root.is_absolute():
        return None
    try:
        return root.resolve()
    except OSError:
        return None


def _boot_file(which: str) -> Path | None:
    """Resolve config.txt / cmdline.txt, never leaving the /boot tree."""
    name = BOOT_FILES.get(which)
    if not name:
        return None
    root = _boot_root()
    if root is None:
        return None

    candidates: list[Path] = []
    current = boot_config_path()
    if current is not None:
        candidates.append(current.parent / name)
    candidates.extend(
        [Path("/boot/firmware") / name, Path("/boot") / name]
    )

    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        if not resolved.is_relative_to(root):
            continue
        if resolved.is_file():
            return resolved

    fallback = (Path("/boot/firmware") / name).resolve(strict=False)
    return fallback if fallback.is_relative_to(root) else None


def _read_boot(path: Path, executor) -> tuple[str, str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace"), ""
    except OSError:
        result = executor.run(
            f"sudo cat {shlex.quote(str(path))} 2>&1", timeout=15
        )
        if not result.ok:
            return "", result.output or f"Could not read {path}"
        return result.stdout, ""


def _write_boot(path: Path, content: str, executor):
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    quoted = shlex.quote(str(path))
    backup = shlex.quote(f"{path}.piweb.bak")
    write = (
        f"printf %s {shlex.quote(encoded)} | base64 -d"
        f" | sudo tee {quoted} >/dev/null"
    )
    if path.is_file():
        command = f"sudo cp {quoted} {backup} && {write}"
    else:
        command = write
    return executor.run(f"{command} 2>&1", timeout=30)


# ── Temperature / CPU-frequency history ─────────────────────────────────────

def _history_file(settings) -> Path:
    # The temperature/frequency history is a short-lived cache, so keep it in
    # command_logs/ (tmpfs on systems configured that way) instead of wearing
    # the SD card every 30 seconds.
    return settings.command_logs_dir / "rpi_metrics_history.json"


def _load_history(path: Path) -> list[dict[str, float | int]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []

    history: list[dict[str, float | int]] = []
    for row in data:
        if not isinstance(row, dict):
            continue
        try:
            history.append(
                {
                    "time": int(row.get("time", 0)),
                    "temp": round(float(row.get("temp", 0)), 1),
                    "freq": int(row.get("freq", 0)),
                }
            )
        except (TypeError, ValueError):
            continue
    return history[-120:]


def _cpu_frequency(executor) -> int:
    raw = ""
    path = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    try:
        raw = path.read_text(encoding="ascii").strip()
    except OSError:
        result = executor.run(
            "cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null",
            timeout=5,
        )
        raw = result.stdout.strip()
    try:
        return int(round(int(raw) / 1000))
    except ValueError:
        return 0


def metrics_history(settings, executor) -> list[dict[str, float | int]]:
    path = _history_file(settings)
    history = _load_history(path)
    now = int(time.time())

    if history and now - int(history[-1]["time"]) < 30:
        return history

    temperature = cpu_temperature(executor)
    history.append(
        {
            "time": now,
            "temp": round(float(temperature), 1) if temperature is not None else 0.0,
            "freq": _cpu_frequency(executor),
        }
    )
    history = history[-120:]

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(history), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass
    return history


# ── Request body helper ─────────────────────────────────────────────────────

async def _payload(request: Request) -> dict[str, object]:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except (ValueError, AttributeError):
        try:
            form = await request.form()
        except Exception:  # noqa: BLE001 - malformed body
            return {}
        return dict(form)


# ── Page ────────────────────────────────────────────────────────────────────

@router.get("/rpi", response_class=HTMLResponse)
def rpi_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    metrics = collect_metrics(executor)
    metric_rows = [
        ("Firmware", metrics["firmware"]),
        ("ARM clock", metrics["arm_clock"]),
        ("Core clock", metrics["core_clock"]),
        ("V3D clock", metrics["v3d_clock"]),
        ("Core voltage", metrics["core_volt"]),
        ("SDRAM voltage", metrics["sdram_volt"]),
        ("ARM memory", metrics["mem_arm"]),
        ("GPU memory", metrics["mem_gpu"]),
    ]
    return render(
        request,
        "rpi.html",
        metrics=metrics,
        metric_rows=metric_rows,
        codecs_text=", ".join(
            f"{name}: {state}" for name, state in metrics["codecs"].items()
        ),
        interfaces=INTERFACES,
        states=interface_states(executor),
    )


# ── JSON endpoints ──────────────────────────────────────────────────────────

@router.get("/ajax/rpi/metrics")
def rpi_metrics(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse({"ok": True, **collect_metrics(executor)})


@router.get("/ajax/rpi/interfaces")
def rpi_interfaces(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse({"ok": True, "states": interface_states(executor)})


@router.post("/ajax/rpi/interface")
async def rpi_interface(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)

    iface = str(data.get("iface", "")).strip().lower()
    try:
        enable = int(data.get("enable", 0))
    except (TypeError, ValueError):
        enable = -1

    if iface not in INTERFACES or enable not in (0, 1):
        return JSONResponse(
            {"ok": False, "error": "Unknown interface or invalid state"},
            status_code=400,
        )

    # raspi-config nonint do_X: 0 = enable, 1 = disable
    argument = "0" if enable == 1 else "1"
    result = executor.run(
        f"sudo raspi-config nonint do_{iface} {argument} 2>&1", timeout=60
    )
    auth_log(
        settings,
        "rpi_interface",
        f"{'enable' if enable else 'disable'} {iface} "
        f"({'ok' if result.ok else 'failed'})",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    if not result.ok:
        return JSONResponse(
            {"ok": False, "error": result.output or "raspi-config failed"},
            status_code=500,
        )
    return JSONResponse(
        {
            "ok": True,
            "message": f"{'Enabled' if enable else 'Disabled'} {iface.upper()}",
            "output": result.output,
        }
    )


@router.get("/ajax/rpi/boot")
def boot_read(request: Request, file: str = "config"):
    settings = request.app.state.settings
    executor = get_executor(settings)
    if file not in BOOT_FILES:
        return JSONResponse({"ok": False, "error": "Unknown file"}, status_code=400)

    path = _boot_file(file)
    if path is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "Boot configuration is only available on Raspberry Pi OS",
            },
            status_code=400,
        )

    content, error = _read_boot(path, executor)
    if error:
        if not path.is_file():
            return JSONResponse(
                {
                    "ok": True,
                    "path": str(path),
                    "content": "",
                    "missing": True,
                    "message": f"{path} does not exist yet — saving will create it.",
                }
            )
        return JSONResponse({"ok": False, "error": error}, status_code=500)
    return JSONResponse({"ok": True, "path": str(path), "content": content})


@router.post("/ajax/rpi/boot")
async def boot_save(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)

    file = str(data.get("file", "config"))
    content = str(data.get("content", ""))
    if file not in BOOT_FILES:
        return JSONResponse({"ok": False, "error": "Unknown file"}, status_code=400)

    path = _boot_file(file)
    if path is None:
        return JSONResponse(
            {
                "ok": False,
                "error": "Boot configuration is only available on Raspberry Pi OS",
            },
            status_code=400,
        )

    had_file = path.is_file()
    result = _write_boot(path, content, executor)
    auth_log(
        settings,
        "boot_config",
        f"save {path} ({'ok' if result.ok else 'failed'})",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    if not result.ok:
        return JSONResponse(
            {"ok": False, "error": result.output or "Could not save the file"},
            status_code=500,
        )

    message = (
        f"Saved (backup: {path}.piweb.bak)"
        if had_file
        else f"Saved {path} (new file, no backup written)"
    )
    return JSONResponse({"ok": True, "path": str(path), "message": message})


@router.get("/ajax/rpi/history")
def rpi_history(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse(
        {"ok": True, "history": metrics_history(settings, executor)}
    )


# ── Disks ───────────────────────────────────────────────────────────────────

@router.get("/ajax/rpi/disks")
def rpi_disks(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse(
        {
            "ok": True,
            "disks": list_disks(executor),
            "mount_root": mount_root(settings),
        }
    )


@router.post("/ajax/rpi/disk/mount")
async def rpi_disk_mount(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)

    device = str(data.get("device", "")).strip()
    name = str(data.get("name", "")).strip()
    read_only = bool(data.get("read_only"))
    if not device:
        return JSONResponse(
            {"ok": False, "error": "A device is required."}, status_code=400
        )

    try:
        message = mount_device(settings, executor, device, name, read_only)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    auth_log(
        settings,
        "disk_mount",
        f"{device} -> {name or 'auto'}{' (ro)' if read_only else ''}",
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "message": message})


@router.post("/ajax/rpi/disk/unmount")
async def rpi_disk_unmount(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    data = await _payload(request)

    device = str(data.get("device", "")).strip()
    if not device:
        return JSONResponse(
            {"ok": False, "error": "A device is required."}, status_code=400
        )

    try:
        message = unmount_device(settings, executor, device)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    auth_log(
        settings,
        "disk_unmount",
        device,
        client_ip(request, settings),
        request.headers.get("user-agent", ""),
    )
    return JSONResponse({"ok": True, "message": message})
