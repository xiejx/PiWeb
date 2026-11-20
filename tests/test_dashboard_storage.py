"""The dashboard storage footer lists physical disks, not every block device.

`lsblk -dn` printed swap loop devices and zram next to the SD card
("loop0 · 2G · loop", "zram0 · 2G · disk"), which told the reader nothing.
"""

from __future__ import annotations

import json

from app.executor import CommandResult
from app.system import block_devices

LSBLK_JSON = json.dumps(
    {
        "blockdevices": [
            {
                "name": "loop0",
                "size": 2147483648,
                "type": "loop",
                "model": None,
                "tran": None,
                "rm": False,
                "mountpoint": None,
            },
            {
                "name": "sda",
                "size": 4000787029504,
                "type": "disk",
                "model": "Portable",
                "tran": "usb",
                "rm": False,
                "mountpoint": None,
                "children": [
                    {"name": "sda1", "size": 134217728, "type": "part", "mountpoint": None},
                    {"name": "sda2", "size": 4000000000000, "type": "part", "mountpoint": "/mnt/hdd"},
                ],
            },
            {
                "name": "mmcblk0",
                "size": 63864569856,
                "type": "disk",
                "model": None,
                "tran": "mmc",
                "rm": False,
                "mountpoint": None,
                "children": [
                    {"name": "mmcblk0p1", "type": "part", "mountpoint": "/boot/firmware"},
                    {"name": "mmcblk0p2", "type": "part", "mountpoint": "/"},
                ],
            },
            {
                "name": "zram0",
                "size": 2147483648,
                "type": "disk",
                "model": None,
                "tran": None,
                "rm": False,
                "mountpoint": "[SWAP]",
            },
        ]
    }
)


class Stub:
    def run(self, command, timeout=30.0, **kwargs):
        assert "lsblk -J" in command, "block_devices must ask for JSON output"
        return CommandResult(stdout=LSBLK_JSON)


def test_pseudo_devices_are_dropped():
    names = [row["name"] for row in block_devices(Stub())]
    assert names == ["sda", "mmcblk0"]


def test_model_transport_and_size_come_along():
    sda = block_devices(Stub())[0]
    assert sda["model"] == "Portable"
    assert sda["transport"] == "usb"
    assert sda["size"] == "3.6 TB"
    assert sda["mounts"] == ["/mnt/hdd"]


def test_root_mount_is_listed_first():
    mmc = block_devices(Stub())[1]
    assert mmc["mounts"] == ["/", "/boot/firmware"]


def test_output_that_is_not_json_is_ignored():
    class Broken:
        def run(self, command, timeout=30.0, **kwargs):
            return CommandResult(stdout="lsblk: not supported")

    assert block_devices(Broken()) == []
