from __future__ import annotations

import re
import shlex
from typing import Any

from .executor import Executor

# ── Helpers ─────────────────────────────────────────────────────────────────

def _split_fields(line: str) -> list[str]:
    """Split nmcli -t output, honouring escaped colons and backslashes."""
    parts: list[str] = []
    buffer: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            buffer.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(char)
    parts.append("".join(buffer))
    return parts


def _mutation(executor: Executor, command: str, timeout: float = 60) -> Any:
    """Run a state-changing command, retrying once through passwordless sudo."""
    result = executor.run(command, timeout=timeout)
    if result.ok:
        return result
    elevated = executor.run(f"sudo -n {command}", timeout=timeout)
    if elevated.returncode == 127:  # sudo missing — keep the original error
        return result
    return elevated if elevated.ok else (elevated if elevated.output else result)


def _available(executor: Executor, name: str) -> bool:
    return executor.run(
        f"command -v {shlex.quote(name)} >/dev/null 2>&1", timeout=8
    ).ok


def backend(executor: Executor) -> str:
    if _available(executor, "nmcli"):
        return "nmcli"
    if _available(executor, "wpa_cli"):
        return "wpa_cli"
    return ""


def _interface(executor: Executor, name: str) -> str:
    if name == "nmcli":
        result = executor.run(
            "nmcli -t -f DEVICE,TYPE device status 2>/dev/null", timeout=10
        )
        for line in result.stdout.splitlines():
            parts = _split_fields(line)
            if len(parts) >= 2 and parts[1].strip() == "wifi":
                return parts[0].strip()

    result = executor.run("cat /proc/net/wireless 2>/dev/null", timeout=5)
    for line in result.stdout.splitlines()[2:]:
        candidate = line.split(":", 1)[0].strip()
        if candidate:
            return candidate

    result = executor.run("ls -1 /sys/class/net 2>/dev/null", timeout=5)
    for candidate in result.stdout.splitlines():
        candidate = candidate.strip()
        if candidate.startswith(("wl", "wlan")):
            return candidate
    return ""


def _signal_percent(dbm: int) -> int:
    return max(0, min(100, 2 * (dbm + 100)))


# ── Status ──────────────────────────────────────────────────────────────────

def status(executor: Executor) -> dict[str, Any]:
    name = backend(executor)
    info: dict[str, Any] = {
        "available": bool(name),
        "backend": name,
        "iface": "",
        "enabled": False,
        "ssid": "",
        "signal": None,
        "saved": [],
        "reason": "",
    }
    if not name:
        info["reason"] = "Neither nmcli (NetworkManager) nor wpa_cli is available."
        return info

    iface = _interface(executor, name)
    info["iface"] = iface

    radio = executor.run("nmcli radio wifi 2>/dev/null", timeout=8).stdout.strip().lower()
    if radio in {"enabled", "disabled"}:
        info["enabled"] = radio == "enabled"
    elif iface:
        rfkill = executor.run("rfkill list wifi 2>/dev/null", timeout=8).output.lower()
        blocked = "blocked: yes" in rfkill
        info["enabled"] = not blocked

    if name == "nmcli":
        result = executor.run(
            "nmcli -t -f IN-USE,SSID,SIGNAL dev wifi list 2>/dev/null", timeout=10
        )
        for line in result.stdout.splitlines():
            parts = _split_fields(line)
            if len(parts) >= 3 and parts[0].strip() == "*":
                info["ssid"] = parts[1].strip()
                try:
                    info["signal"] = int(parts[2].strip())
                except ValueError:
                    info["signal"] = None
                break
        saved = executor.run(
            "nmcli -t -f NAME,TYPE connection show 2>/dev/null", timeout=10
        )
        for line in saved.stdout.splitlines():
            parts = _split_fields(line)
            if len(parts) >= 2 and parts[1].strip() in {
                "802-11-wireless",
                "wifi",
            }:
                if parts[0].strip():
                    info["saved"].append(parts[0].strip())
    elif iface:
        quoted = shlex.quote(iface)
        result = executor.run(f"wpa_cli -i {quoted} status 2>/dev/null", timeout=10)
        for line in result.stdout.splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "ssid":
                info["ssid"] = value.strip()
            elif key.strip() == "wpa_state" and value.strip() == "COMPLETED":
                info["enabled"] = True
        sig = executor.run(
            f"iwconfig {quoted} 2>/dev/null | grep -o 'Signal level=[^ ]*'", timeout=8
        )
        match = re.search(r"Signal level=(-?\d+)", sig.stdout)
        if match:
            info["signal"] = _signal_percent(int(match.group(1)))
        saved = executor.run(
            f"wpa_cli -i {quoted} list_networks 2>/dev/null", timeout=10
        )
        for line in saved.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip():
                info["saved"].append(parts[1].strip())

    info["saved"] = sorted(set(info["saved"]))
    return info


# ── Scan ────────────────────────────────────────────────────────────────────

def scan(executor: Executor) -> list[dict[str, Any]]:
    name = backend(executor)
    if not name:
        raise RuntimeError("Wi-Fi tooling is not installed (nmcli or wpa_cli).")

    networks: dict[str, dict[str, Any]] = {}

    if name == "nmcli":
        # `list --rescan yes` still exits 0 when NetworkManager refuses the
        # scan ("not authorized"); it just prints the stale BSS cache, which
        # often contains nothing but the connected network. Probe the rescan
        # separately to learn whether escalation is required, then let the
        # blocking list wait for the scan to finish instead of sleeping.
        rescan = executor.run("nmcli device wifi rescan 2>/dev/null", timeout=25)
        command = (
            "nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY device wifi list "
            "--rescan yes 2>/dev/null"
        )
        if rescan.ok:
            result = executor.run(command, timeout=45)
        else:
            result = executor.run(f"sudo -n {command}", timeout=45)
            if not result.stdout.strip():
                result = executor.run(command, timeout=45)
        lines = result.stdout.splitlines()
        for line in lines:
            parts = _split_fields(line)
            if len(parts) < 4:
                continue
            active, ssid, signal, security = parts[0], parts[1], parts[2], parts[3]
            if not ssid.strip():
                continue
            try:
                strength = int(signal.strip())
            except ValueError:
                strength = 0
            entry = networks.get(ssid)
            if entry is None or strength > int(entry["signal"]):
                networks[ssid] = {
                    "ssid": ssid,
                    "signal": strength,
                    "security": security.strip() or "Open",
                    "active": active.strip() == "*",
                }
    else:
        iface = _interface(executor, name)
        if not iface:
            raise RuntimeError("No wireless interface was found.")
        quoted = shlex.quote(iface)
        _mutation(executor, f"wpa_cli -i {quoted} scan", timeout=20)
        executor.run("sleep 4", timeout=10)
        result = executor.run(f"wpa_cli -i {quoted} scan_results 2>/dev/null", timeout=15)
        for line in result.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            try:
                strength = _signal_percent(int(parts[2]))
            except ValueError:
                strength = 0
            ssid = parts[4].strip()
            if not ssid:
                continue
            flags = parts[3].upper()
            security = "Open"
            if "WPA3" in flags:
                security = "WPA3"
            elif "WPA2" in flags:
                security = "WPA2"
            elif "WPA" in flags:
                security = "WPA"
            elif "WEP" in flags:
                security = "WEP"
            entry = networks.get(ssid)
            if entry is None or strength > int(entry["signal"]):
                networks[ssid] = {
                    "ssid": ssid,
                    "signal": strength,
                    "security": security,
                    "active": False,
                }

    return sorted(networks.values(), key=lambda row: int(row["signal"]), reverse=True)


# ── Actions ─────────────────────────────────────────────────────────────────

def connect(executor: Executor, ssid: str, password: str = "") -> str:
    ssid = (ssid or "").strip()
    password = password or ""
    if not ssid or len(ssid) > 32:
        raise ValueError("SSID must be between 1 and 32 characters.")
    if password and not (8 <= len(password) <= 63):
        raise ValueError("The Wi-Fi password must be 8–63 characters long.")

    name = backend(executor)
    if not name:
        raise RuntimeError("Wi-Fi tooling is not installed (nmcli or wpa_cli).")

    if name == "nmcli":
        if password:
            command = (
                f"nmcli device wifi connect {shlex.quote(ssid)} "
                f"password {shlex.quote(password)}"
            )
        else:
            command = f"nmcli connection up id {shlex.quote(ssid)}"
        result = _mutation(executor, command, timeout=75)
        if not result.ok and not password:
            result = _mutation(
                executor, f"nmcli device wifi connect {shlex.quote(ssid)}", timeout=75
            )
        if not result.ok:
            raise RuntimeError(result.output or "NetworkManager could not connect.")
        return f"Connected to {ssid}."

    iface = _interface(executor, name)
    if not iface:
        raise RuntimeError("No wireless interface was found.")
    quoted_iface = shlex.quote(iface)

    if not password:
        listing = executor.run(
            f"wpa_cli -i {quoted_iface} list_networks 2>/dev/null", timeout=10
        )
        for line in listing.stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip() == ssid:
                _mutation(
                    executor,
                    f"wpa_cli -i {quoted_iface} select_network {parts[0].strip()}",
                    timeout=40,
                )
                return f"Connected to {ssid}."

    add = _mutation(executor, f"wpa_cli -i {quoted_iface} add_network", timeout=30)
    network_id = ""
    for line in reversed(add.stdout.splitlines()):
        if line.strip().isdigit():
            network_id = line.strip()
            break
    if not network_id:
        raise RuntimeError(add.output or "wpa_supplicant did not return a network id.")

    def _wpa_value(value: str) -> str:
        return shlex.quote('"' + value.replace('"', "") + '"')

    commands = [
        f"set_network {network_id} ssid {_wpa_value(ssid)}",
    ]
    if password:
        commands.append(f"set_network {network_id} psk {_wpa_value(password)}")
    else:
        commands.append(f"set_network {network_id} key_mgmt NONE")
    commands.extend(
        [f"enable_network {network_id}", "save_config", "reconfigure"]
    )
    for command in commands:
        result = _mutation(
            executor, f"wpa_cli -i {quoted_iface} {command}", timeout=40
        )
        if not result.ok or "FAIL" in result.output.upper():
            raise RuntimeError(result.output or "wpa_supplicant rejected the network.")
    return f"Connected to {ssid}."


def disconnect(executor: Executor) -> str:
    name = backend(executor)
    if not name:
        raise RuntimeError("Wi-Fi tooling is not installed.")
    if name == "nmcli":
        iface = _interface(executor, name)
        if not iface:
            raise RuntimeError("No wireless interface was found.")
        result = _mutation(
            executor, f"nmcli device disconnect {shlex.quote(iface)}", timeout=40
        )
        if not result.ok:
            raise RuntimeError(result.output or "Could not disconnect.")
        return f"Disconnected {iface}."
    iface = _interface(executor, name)
    if not iface:
        raise RuntimeError("No wireless interface was found.")
    result = _mutation(
        executor, f"wpa_cli -i {shlex.quote(iface)} disconnect", timeout=40
    )
    if not result.ok:
        raise RuntimeError(result.output or "Could not disconnect.")
    return f"Disconnected {iface}."


def set_radio(executor: Executor, enabled: bool) -> str:
    name = backend(executor)
    if not name:
        raise RuntimeError("Wi-Fi tooling is not installed.")
    if name == "nmcli":
        command = f"nmcli radio wifi {'on' if enabled else 'off'}"
        result = _mutation(executor, command, timeout=30)
        if not result.ok:
            raise RuntimeError(result.output or "Could not change the radio state.")
        return "Wi-Fi enabled." if enabled else "Wi-Fi disabled."

    iface = _interface(executor, name)
    command = (
        f"rfkill {'unblock' if enabled else 'block'} wifi"
        if not iface
        else f"ip link set {shlex.quote(iface)} {'up' if enabled else 'down'}"
    )
    result = _mutation(executor, command, timeout=30)
    if not result.ok:
        raise RuntimeError(result.output or "Could not change the radio state.")
    return "Wi-Fi enabled." if enabled else "Wi-Fi disabled."
