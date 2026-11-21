"""Tests for local disk mounting.

NTFS has two local drivers on Raspberry Pi OS: the in-kernel ntfs3 and the
FUSE ntfs-3g fallback. The page must try ntfs3 first so modern systems get the
faster driver, while older kernels without it still mount through ntfs-3g.
"""

from __future__ import annotations

import json

from app.config import Settings
from app.executor import CommandResult
from app.storage import mount_device

LSBLK = json.dumps(
    {
        "blockdevices": [
            {
                "name": "sda1",
                "path": "/dev/sda1",
                "type": "part",
                "size": 1000,
                "fstype": "ntfs",
                "label": "disk",
                "mountpoint": None,
                "rm": True,
                "hotplug": True,
                "tran": "usb",
            }
        ]
    }
)


class MountStub:
    """Answers lsblk from a canned table and lets a test fail chosen mounts."""

    def __init__(self, fstype: str = "ntfs", fail: set[str] | None = None) -> None:
        table = json.loads(LSBLK)
        table["blockdevices"][0]["fstype"] = fstype
        self.lsblk = json.dumps(table)
        self.fail = fail or set()
        self.commands: list[str] = []

    def run(self, command: str, timeout: float = 30.0, **kwargs) -> CommandResult:
        self.commands.append(command)
        if command.startswith("lsblk"):
            return CommandResult(stdout=self.lsblk, returncode=0)
        if any(token in command for token in self.fail):
            return CommandResult(stderr="mount failed", returncode=32)
        return CommandResult(stdout="ok", returncode=0)


def settings() -> Settings:
    return Settings(data_dir="/tmp/piweb-test-data")


def ntfs_commands(stub: MountStub) -> list[str]:
    return [cmd for cmd in stub.commands if "mount " in cmd or "ntfs-3g " in cmd]


def test_ntfs_uses_kernel_ntfs3_first():
    stub = MountStub(fstype="ntfs")
    mount_device(settings(), stub, "/dev/sda1")

    mount_calls = ntfs_commands(stub)
    assert len(mount_calls) == 1  # succeeded on the first attempt
    assert "mount -t ntfs3" in mount_calls[0]
    assert "/dev/sda1" in mount_calls[0]


def test_ntfs_falls_back_to_ntfs_3g_when_ntfs3_fails():
    stub = MountStub(fstype="ntfs", fail={"ntfs3"})
    mount_device(settings(), stub, "/dev/sda1")

    mount_calls = ntfs_commands(stub)
    assert len(mount_calls) == 2
    assert "mount -t ntfs3" in mount_calls[0]
    assert "ntfs-3g" in mount_calls[1]
    assert "ntfs3" not in mount_calls[1]  # fallback is the FUSE driver


def test_ntfs_read_only_option_on_both_drivers():
    stub = MountStub(fstype="ntfs", fail={"ntfs3"})
    mount_device(settings(), stub, "/dev/sda1", read_only=True)

    mount_calls = ntfs_commands(stub)
    assert all("-o ro" in cmd for cmd in mount_calls)


def test_non_ntfs_keeps_plain_mount():
    stub = MountStub(fstype="ext4")
    mount_device(settings(), stub, "/dev/sda1")

    mount_calls = ntfs_commands(stub)
    assert len(mount_calls) == 1
    assert mount_calls[0].startswith("sudo -n mount ")
    assert "-t" not in mount_calls[0]


def test_ntfs_raises_when_both_drivers_fail():
    stub = MountStub(fstype="ntfs", fail={"ntfs3", "ntfs-3g"})
    try:
        mount_device(settings(), stub, "/dev/sda1")
    except RuntimeError as exc:
        assert "mount failed" in str(exc)
    else:
        raise AssertionError("expected a RuntimeError when both drivers fail")
    assert len(ntfs_commands(stub)) == 2
