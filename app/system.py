from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import threading
import time
from pathlib import Path
from typing import Any, Sequence

from .config import Settings
from .executor import Executor, format_bytes, format_duration, read_text


# ARM CPU part values reported by /proc/cpuinfo (implementer 0x41 = ARM Ltd.).
# Raspberry Pi OS exposes these on ARM kernels but has no generic "model name"
# field; the "Model" line describes the board, not the CPU core.
_ARM_CPU_PARTS = {
    "0xb76": "ARM1176JZF-S",
    "0xc07": "ARM Cortex-A7",
    "0xd03": "ARM Cortex-A53",
    "0xd07": "ARM Cortex-A57",
    "0xd08": "ARM Cortex-A72",
    "0xd09": "ARM Cortex-A73",
    "0xd0a": "ARM Cortex-A75",
    "0xd0b": "ARM Cortex-A76",
}


# ── CPU ───────────────────────────────────────────────────────────────────────

def _cpu_sample() -> tuple[int, int]:
    try:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()
        values = [int(v) for v in fields[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        total = sum(values)
        return total, idle
    except (OSError, ValueError, IndexError):
        return 0, 0


_last_cpu: tuple[int, int, float] | None = None


def cpu_usage() -> float:
    """CPU busy percentage since the previous call (0 when not measurable)."""
    global _last_cpu
    total, idle = _cpu_sample()
    if not total:
        return 0.0
    previous = _last_cpu
    _last_cpu = (total, idle, time.time())
    if previous is None:
        # First sample: take a short in-process measurement so the initial
        # dashboard render is meaningful instead of a flat 0%.
        time.sleep(0.3)
        total2, idle2 = _cpu_sample()
        if total2 > total:
            _last_cpu = (total2, idle2, time.time())
            return round(
                max(0.0, min(100.0, (1 - (idle2 - idle) / (total2 - total)) * 100)),
                1,
            )
        return 0.0
    total_delta = total - previous[0]
    idle_delta = idle - previous[1]
    if total_delta <= 0:
        return 0.0
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def load_average() -> list[float]:
    try:
        return [round(v, 2) for v in os.getloadavg()]
    except (AttributeError, OSError):
        return [0.0, 0.0, 0.0]


def cpu_count() -> int:
    return os.cpu_count() or 1


def _cpuinfo_field(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else ""


def cpu_model() -> str:
    """Return the CPU core name, never the Raspberry Pi board model.

    Raspberry Pi OS puts a ``Model`` line in /proc/cpuinfo (e.g.
    "Raspberry Pi 4 Model B Rev 1.4") but no ``model name``/``Hardware`` line.
    The old implementation matched ``Model`` first and reported the board as
    the CPU. Resolve ARM cores from CPU implementer/part instead.
    """
    text = read_text("/proc/cpuinfo")

    implementer = _cpuinfo_field(text, "CPU implementer").lower()
    part = _cpuinfo_field(text, "CPU part").lower()
    if implementer == "0x41" and part:
        core = _ARM_CPU_PARTS.get(part)
        if core:
            return core
        return f"ARM CPU part {part}"

    for key in ("model name", "Hardware", "Processor"):
        value = _cpuinfo_field(text, key)
        if value:
            return value
    return platform.processor() or "Unknown"


def pi_model() -> str:
    try:
        raw = Path("/proc/device-tree/model").read_bytes().replace(b"\x00", b"")
        return raw.decode("utf-8", "replace").strip()
    except OSError:
        return ""


# ── Memory / disk ─────────────────────────────────────────────────────────────

def memory_info() -> dict[str, Any]:
    info: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                info[key] = int(parts[0]) * 1024
    except (OSError, ValueError):
        pass

    total = info.get("MemTotal", 0)
    free = info.get("MemFree", 0)
    available = info.get("MemAvailable", free)
    buffers = info.get("Buffers", 0)
    cached = info.get("Cached", 0)
    used = max(0, total - available)
    swap_total = info.get("SwapTotal", 0)
    swap_free = info.get("SwapFree", 0)
    swap_used = max(0, swap_total - swap_free)
    return {
        "total": total,
        "total_h": format_bytes(total),
        "free": free,
        "free_h": format_bytes(free),
        "available": available,
        "available_h": format_bytes(available),
        "used": used,
        "used_h": format_bytes(used),
        "buffers": buffers,
        "buffers_h": format_bytes(buffers),
        "cached": cached,
        "cached_h": format_bytes(cached),
        "percent": round(used / total * 100, 1) if total else 0.0,
        "swap_total": swap_total,
        "swap_total_h": format_bytes(swap_total),
        "swap_used": swap_used,
        "swap_used_h": format_bytes(swap_used),
        "swap_percent": round(swap_used / swap_total * 100, 1) if swap_total else 0.0,
    }


def disk_info(path: str = "/") -> dict[str, Any]:
    try:
        usage = os.statvfs(path)
        total = usage.f_blocks * usage.f_frsize
        free = usage.f_bavail * usage.f_frsize
        used = total - usage.f_bfree * usage.f_frsize
    except (OSError, AttributeError):
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return {
                "total": 0, "total_h": "n/a", "used": 0, "used_h": "n/a",
                "free": 0, "free_h": "n/a", "percent": 0.0,
            }
        total, free, used = usage.total, usage.free, usage.used
    percent = round(used / total * 100, 1) if total else 0.0
    return {
        "total": total,
        "total_h": format_bytes(total),
        "used": used,
        "used_h": format_bytes(used),
        "free": free,
        "free_h": format_bytes(free),
        "percent": percent,
    }


def top_partitions(executor: Executor) -> list[dict[str, str]]:
    result = executor.run("df -h -x tmpfs -x devtmpfs -x squashfs 2>/dev/null", timeout=10)
    rows: list[dict[str, str]] = []
    lines = result.stdout.strip().splitlines()
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 6:
            rows.append(
                {
                    "filesystem": parts[0],
                    "size": parts[1],
                    "used": parts[2],
                    "available": parts[3],
                    "percent": parts[4],
                    "mount": " ".join(parts[5:]),
                }
            )
    return rows


def block_devices(executor: Executor) -> list[dict[str, str]]:
    result = executor.run(
        "lsblk -dn -o NAME,SIZE,TYPE,MODEL 2>/dev/null", timeout=10
    )
    rows = []
    for line in result.stdout.strip().splitlines():
        parts = line.split(None, 3)
        if len(parts) >= 3:
            rows.append(
                {
                    "name": parts[0],
                    "size": parts[1],
                    "type": parts[2],
                    "model": parts[3] if len(parts) > 3 else "",
                }
            )
    return rows


def usb_devices(executor: Executor) -> list[str]:
    result = executor.run("lsusb 2>/dev/null", timeout=10)
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ── Temperature / throttling ─────────────────────────────────────────────────

def cpu_temperature(executor: Executor) -> float | None:
    for zone in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            value = int(zone.read_text(encoding="ascii").strip())
            if value > 1000:
                return round(value / 1000.0, 1)
            return float(value)
        except (OSError, ValueError):
            continue
    result = executor.run("vcgencmd measure_temp 2>/dev/null", timeout=5)
    match = re.search(r"temp=([0-9.]+)", result.stdout)
    if match:
        return float(match.group(1))
    return None


def throttled_status(executor: Executor) -> dict[str, Any]:
    result = executor.run("vcgencmd get_throttled 2>/dev/null", timeout=5)
    match = re.search(r"throttled=(0x[0-9a-fA-F]+)", result.stdout)
    if not match:
        return {"available": False, "flags": [], "healthy": None}
    bits = int(match.group(1), 16)
    labels = {
        0: "Under-voltage detected",
        1: "CPU frequency capped",
        2: "Currently throttled",
        3: "Soft temperature limit active",
        16: "Under-voltage has occurred",
        17: "CPU frequency capping has occurred",
        18: "Throttling has occurred",
        19: "Soft temperature limit has occurred",
    }
    flags = [label for bit, label in labels.items() if bits & (1 << bit)]
    return {"available": True, "flags": flags, "healthy": not flags, "raw": match.group(1)}


def gpu_temperature(executor: Executor) -> float | None:
    result = executor.run("vcgencmd measure_temp 2>/dev/null", timeout=5)
    match = re.search(r"temp=([0-9.]+)", result.stdout)
    return float(match.group(1)) if match else None


# ── System identity ──────────────────────────────────────────────────────────

def uptime_seconds() -> float:
    try:
        return float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
    except (OSError, ValueError, IndexError):
        return 0.0


def os_name() -> str:
    text = read_text("/etc/os-release")
    match = re.search(r'^PRETTY_NAME="?([^"\n]+)"?', text, re.MULTILINE)
    if match:
        return match.group(1)
    return platform.system() or "Unknown"


def kernel() -> str:
    return platform.release()


def hostname() -> str:
    return socket.gethostname()


def system_info(executor: Executor) -> dict[str, str]:
    uptime = uptime_seconds()
    return {
        "hostname": hostname(),
        "os": os_name(),
        "kernel": kernel(),
        "cpu": cpu_model(),
        "cores": str(cpu_count()),
        "uptime": format_duration(uptime) if uptime else "Unknown",
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S"),
        "load": " ".join(f"{v:.2f}" for v in load_average()),
        "model": pi_model(),
    }


def process_count(executor: Executor) -> int:
    result = executor.run("ps -e --no-headers 2>/dev/null | wc -l", timeout=10)
    try:
        return int(result.stdout.strip())
    except ValueError:
        return 0


# ── Network ──────────────────────────────────────────────────────────────────

def _net_counters() -> dict[str, tuple[int, int]]:
    counters: dict[str, tuple[int, int]] = {}
    try:
        lines = Path("/proc/net/dev").read_text(encoding="utf-8").splitlines()[2:]
    except OSError:
        return counters
    for line in lines:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 9:
            counters[name.strip()] = (int(fields[0]), int(fields[8]))
    return counters


def network_interfaces(executor: Executor) -> list[dict[str, Any]]:
    counters = _net_counters()
    result = executor.run(
        "ip -o -4 addr show 2>/dev/null || true", timeout=10
    )
    addresses: dict[str, str] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "inet":
            addresses.setdefault(parts[1], parts[3])

    interfaces: list[dict[str, Any]] = []
    net_dir = Path("/sys/class/net")
    if not net_dir.is_dir():
        return interfaces
    for entry in sorted(net_dir.iterdir()):
        name = entry.name
        if name == "lo":
            continue
        try:
            state = (entry / "operstate").read_text(encoding="ascii").strip()
        except OSError:
            state = "unknown"
        try:
            mac = (entry / "address").read_text(encoding="ascii").strip()
        except OSError:
            mac = ""
        rx, tx = counters.get(name, (0, 0))
        signal = ""
        wireless_dir = entry / "wireless"
        if wireless_dir.exists():
            sig = executor.run(
                f"iwconfig {name} 2>/dev/null | grep -o 'Signal level=[^ ]*'", timeout=5
            )
            match = re.search(r"Signal level=(-?\d+)", sig.stdout)
            if match:
                dbm = int(match.group(1))
                signal = f"{dbm} dBm ({max(0, min(100, 2 * (dbm + 100)))}%)"
        interfaces.append(
            {
                "name": name,
                "ipv4": addresses.get(name, "—"),
                "state": state,
                "mac": mac,
                "signal": signal,
                "rx": rx,
                "rx_h": format_bytes(rx),
                "tx": tx,
                "tx_h": format_bytes(tx),
            }
        )
    return interfaces


# ── Network throughput ──────────────────────────────────────────────────────

_net_rate_lock = threading.Lock()
_net_rate_sample: tuple[float, dict[str, tuple[int, int]]] | None = None
_net_rate_values: dict[str, dict[str, float]] = {}


def _remote_net_counters(executor: Executor) -> dict[str, tuple[int, int]]:
    counters: dict[str, tuple[int, int]] = {}
    result = executor.run("cat /proc/net/dev 2>/dev/null", timeout=5)
    for line in result.stdout.splitlines()[2:]:
        name, _, rest = line.partition(":")
        fields = rest.split()
        if len(fields) >= 9:
            try:
                counters[name.strip()] = (int(fields[0]), int(fields[8]))
            except ValueError:
                continue
    return counters


def network_rates(executor: Executor) -> dict[str, dict[str, float]]:
    """Bytes per second per interface, sampled from consecutive calls."""
    global _net_rate_sample, _net_rate_values

    counters = _remote_net_counters(executor)
    now = time.monotonic()
    with _net_rate_lock:
        previous = _net_rate_sample
        if previous is not None and 0 <= now - previous[0] < 0.8:
            return dict(_net_rate_values)
        if not counters:
            return dict(_net_rate_values)
        _net_rate_sample = (now, counters)
        if previous is None:
            _net_rate_values = {}
            return {}

        elapsed = now - previous[0]
        rates: dict[str, dict[str, float]] = {}
        if elapsed > 0:
            for name, (rx, tx) in counters.items():
                prev_rx, prev_tx = previous[1].get(name, (rx, tx))
                rates[name] = {
                    "rx": max(0.0, (rx - prev_rx) / elapsed),
                    "tx": max(0.0, (tx - prev_tx) / elapsed),
                }
        _net_rate_values = rates
        return dict(rates)


# ── Clock / NTP ─────────────────────────────────────────────────────────────

def clock_info(executor: Executor) -> dict[str, Any]:
    date_text = executor.run(
        "date '+%Y-%m-%d %H:%M:%S' 2>/dev/null", timeout=5
    ).stdout.strip()
    timezone = executor.run(
        "timedatectl show -p Timezone --value 2>/dev/null", timeout=5
    ).stdout.strip()
    ntp_raw = executor.run(
        "timedatectl show -p NTPSynchronized --value 2>/dev/null", timeout=5
    ).stdout.strip().lower()

    if not date_text:
        date_text = time.strftime("%Y-%m-%d %H:%M:%S")
    synced = True if ntp_raw == "yes" else False if ntp_raw == "no" else None
    return {
        "datetime": date_text,
        "timezone": timezone or "—",
        "ntp_synced": synced,
    }


# ── Services ─────────────────────────────────────────────────────────────────

def service_state(executor: Executor, name: str) -> str:
    result = executor.run(f"systemctl is-active {name} 2>/dev/null", timeout=5)
    state = result.stdout.strip() or "unknown"
    if state not in {"active", "inactive", "failed", "activating", "deactivating"}:
        return "unknown"
    return state


def dashboard_service_states(settings: Settings, executor: Executor) -> list[dict[str, str]]:
    return [
        {"name": name, "state": service_state(executor, name)}
        for name in settings.dashboard_services
    ]


# Unit types the Services page shows, in display order. `device`, `slice` and
# `scope` are deliberately absent: they are either generated by the kernel in
# their hundreds or cannot be started/stopped meaningfully.
UNIT_TYPES = (
    "service",
    "socket",
    "timer",
    "mount",
    "automount",
    "path",
    "swap",
    "target",
)


def unit_type(unit: str) -> str:
    """Return the unit suffix ("service", "timer", …) or "" for junk lines."""
    if "." not in unit:
        return ""
    return unit.rsplit(".", 1)[-1]


def unit_basename(unit: str) -> str:
    """`getty@tty1.service` -> `getty@tty1`, `-.mount` -> `-`."""
    suffix = unit_type(unit)
    return unit[: -(len(suffix) + 1)] if suffix else unit


def _type_flags(unit_types: Sequence[str] | None) -> tuple[str, tuple[str, ...]]:
    types = tuple(unit_types) if unit_types else UNIT_TYPES
    return " ".join(f"--type={name}" for name in types), types


def list_units(
    executor: Executor, unit_types: Sequence[str] | None = None
) -> list[dict[str, str]]:
    """Runtime state for every unit of the requested types, in one call.

    `--type` may be repeated, so asking for eight types costs one `systemctl`
    invocation instead of eight.
    """
    flags, types = _type_flags(unit_types)
    result = executor.run(
        f"systemctl list-units {flags} --all --no-legend --no-pager --plain 2>/dev/null",
        timeout=20,
    )
    units = []
    for line in result.stdout.splitlines():
        # --plain omits the state bullets on current systemd, but older builds
        # still emit them (● active, × failed, ○ not-found).
        line = line.strip().lstrip("●×○*").strip()
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        unit, load, active, sub = parts[:4]
        kind = unit_type(unit)
        if kind not in types:
            continue  # also drops the "N loaded units listed." trailer
        units.append(
            {
                "unit": unit,
                "name": unit_basename(unit),
                "type": kind,
                "load": load,
                "active": active,
                "sub": sub,
                "description": parts[4] if len(parts) > 4 else "",
            }
        )
    return units


def list_unit_files(
    executor: Executor, unit_types: Sequence[str] | None = None
) -> dict[str, dict[str, str]]:
    """Map unit name -> {state, preset} from `systemctl list-unit-files`.

    `list-units` reports the runtime state but not whether a unit is started at
    boot; this answers that (enabled / disabled / static / masked).
    """
    flags, types = _type_flags(unit_types)
    result = executor.run(
        f"systemctl list-unit-files {flags} --no-legend --no-pager --plain 2>/dev/null",
        timeout=20,
    )
    states: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        unit, state = parts[0], parts[1]
        if unit_type(unit) not in types:
            continue  # skips the "N unit files listed." trailer
        states[unit] = {"state": state, "preset": parts[2] if len(parts) > 2 else ""}
    return states



# ── Processes ────────────────────────────────────────────────────────────────

def list_processes(executor: Executor) -> list[dict[str, str]]:
    result = executor.run(
        "ps -eo pid,ppid,user:20,pcpu,pmem,rss,stat,etime,args --sort=-pcpu --no-headers 2>/dev/null",
        timeout=15,
    )
    processes = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 8)
        if len(parts) < 8:
            continue
        pid, ppid, user, cpu, mem, rss, stat = parts[:7]
        etime = parts[7]
        command = parts[8] if len(parts) > 8 else ""
        processes.append(
            {
                "pid": pid,
                "ppid": ppid,
                "user": user,
                "cpu": cpu,
                "mem": mem,
                "rss": rss,
                "stat": stat,
                "etime": etime,
                "command": command,
            }
        )
    return processes


def top_processes(executor: Executor, limit: int = 5) -> list[dict[str, str]]:
    processes = list_processes(executor)
    processes.sort(key=lambda p: float(p["mem"] or 0), reverse=True)
    return processes[:limit]


def active_users(executor: Executor) -> list[dict[str, str]]:
    result = executor.run("who 2>/dev/null", timeout=10)
    users = []
    for line in result.stdout.splitlines():
        parts = line.split(None, 4)
        if not parts:
            continue
        users.append(
            {
                "user": parts[0],
                "tty": parts[1] if len(parts) > 1 else "",
                "since": " ".join(parts[2:4]) if len(parts) > 3 else "",
                "from": parts[4].strip("()") if len(parts) > 4 else "",
            }
        )
    return users


# ── Raspberry Pi ─────────────────────────────────────────────────────────────

def vcgencmd(executor: Executor, command: str) -> str:
    result = executor.run(f"vcgencmd {command} 2>/dev/null", timeout=5)
    return result.stdout.strip()


def arm_frequency(executor: Executor) -> str:
    output = vcgencmd(executor, "measure_clock arm")
    match = re.search(r"=(\d+)", output)
    return f"{int(match.group(1)) // 1_000_000} MHz" if match else "n/a"


def core_voltage(executor: Executor) -> str:
    output = vcgencmd(executor, "measure_volts core")
    match = re.search(r"=([0-9.]+)V", output)
    return f"{match.group(1)} V" if match else "n/a"


def firmware_version(executor: Executor) -> str:
    for path in ("/boot/firmware/config.txt", "/boot/config.txt"):
        if Path(path).is_file():
            return read_text(path)
    return ""


def boot_config_path() -> Path | None:
    for candidate in (
        Path("/boot/firmware/config.txt"),
        Path("/boot/config.txt"),
    ):
        if candidate.is_file():
            return candidate
    return None
