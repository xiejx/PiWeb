from __future__ import annotations

import json
import re
import shlex
from typing import Any

from .config import Settings
from .executor import Executor, format_bytes

NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def mount_root(settings: Settings) -> str:
    section = settings.raw_config.get("storage", {})
    value = section.get("mount_root", "/mnt") if isinstance(section, dict) else "/mnt"
    value = str(value or "/mnt").strip()
    if not value.startswith("/") or value == "/":
        return "/mnt"
    return value.rstrip("/") or "/mnt"


def _run_elevated(executor: Executor, command: str, timeout: float = 60) -> Any:
    result = executor.run(f"sudo -n {command} 2>&1", timeout=timeout)
    if result.ok:
        return result
    if result.returncode == 127:
        return executor.run(f"{command} 2>&1", timeout=timeout)
    return result


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _mountpoint(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "").strip()


def _flatten(nodes: Any):
    if not isinstance(nodes, list):
        return
    for node in nodes:
        if not isinstance(node, dict):
            continue
        yield node
        yield from _flatten(node.get("children"))


def list_disks(executor: Executor) -> list[dict[str, Any]]:
    result = executor.run(
        "lsblk -J -b -o NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,MOUNTPOINT,RM,HOTPLUG,TRAN "
        "2>/dev/null",
        timeout=20,
    )
    if not result.ok or not result.stdout.strip():
        return []
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return []

    disks: list[dict[str, Any]] = []
    for node in _flatten(payload.get("blockdevices")):
        path = str(node.get("path") or "")
        kind = str(node.get("type") or "")
        if not path or kind in {"loop", "rom"}:
            continue
        mountpoint = _mountpoint(node.get("mountpoint"))
        disks.append(
            {
                "name": str(node.get("name") or ""),
                "path": path,
                "type": kind,
                "size": _as_int(node.get("size")),
                "size_h": format_bytes(_as_int(node.get("size"))),
                "fstype": str(node.get("fstype") or ""),
                "label": str(node.get("label") or "").strip(),
                "mountpoint": mountpoint,
                "removable": bool(node.get("rm")) or bool(node.get("hotplug")),
                "transport": str(node.get("tran") or ""),
                "mounted": bool(mountpoint),
            }
        )
    return disks


def _find(disks: list[dict[str, Any]], device: str) -> dict[str, Any]:
    for disk in disks:
        if disk["path"] == device:
            return disk
    raise ValueError("Unknown device.")


def _target_name(disk: dict[str, Any], name: str) -> str:
    candidate = (name or disk.get("label") or disk.get("name") or "disk").strip()
    candidate = NAME_RE.sub("-", candidate).strip("-._")
    if not candidate:
        candidate = f"disk-{disk.get('name') or 'usb'}"
    return candidate[:48]


def mount_device(
    settings: Settings,
    executor: Executor,
    device: str,
    name: str = "",
    read_only: bool = False,
) -> str:
    if not device.startswith("/dev/"):
        raise ValueError("The device path must start with /dev/.")
    disks = list_disks(executor)
    disk = _find(disks, device)
    if not disk["fstype"]:
        raise ValueError("The device has no recognised filesystem.")
    if disk["mountpoint"]:
        raise ValueError(f"The device is already mounted on {disk['mountpoint']}.")

    root = mount_root(settings)
    target = f"{root}/{_target_name(disk, name)}"
    if any(other["mountpoint"] == target for other in disks):
        raise ValueError(f"{target} is already used by another mount.")
    quoted_device = shlex.quote(device)
    quoted_target = shlex.quote(target)
    options = "-o ro" if read_only else ""

    prepared = _run_elevated(executor, f"mkdir -p {quoted_target}", timeout=30)
    if not prepared.ok:
        raise RuntimeError(prepared.output or "Could not create the mount folder.")

    result = _run_elevated(
        executor, f"mount {options} {quoted_device} {quoted_target}", timeout=60
    )
    if not result.ok and disk["fstype"].startswith("ntfs"):
        result = _run_elevated(
            executor,
            f"ntfs-3g {options} {quoted_device} {quoted_target}",
            timeout=60,
        )
    if not result.ok:
        raise RuntimeError(result.output or "Could not mount the device.")
    return f"Mounted {device} on {target}."


def unmount_device(settings: Settings, executor: Executor, device: str) -> str:
    if not device.startswith("/dev/"):
        raise ValueError("The device path must start with /dev/.")
    disk = _find(list_disks(executor), device)
    mountpoint = disk["mountpoint"]
    if not mountpoint:
        raise ValueError("The device is not mounted.")

    root = mount_root(settings).rstrip("/")
    resolved = mountpoint.rstrip("/")
    if not resolved.startswith(f"{root}/") or resolved == root:
        raise ValueError("Refusing to unmount a system path.")

    result = _run_elevated(executor, f"umount {shlex.quote(resolved)}", timeout=60)
    if not result.ok:
        raise RuntimeError(result.output or "Could not unmount the device.")
    return f"Unmounted {resolved}."
