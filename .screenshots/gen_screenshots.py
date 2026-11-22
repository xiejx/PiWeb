"""Generate README screenshots with entirely fictional data.

Starts the real PiWeb app via uvicorn, but replaces every system-data source
with synthetic values. The templates, CSS and JS are the project's own, so the
screenshots are faithful to the actual UI while containing no real host data.

Run:  python .screenshots/gen_screenshots.py
Then point Chrome --headless --screenshot at http://127.0.0.1:8791/<page>
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import config as config_mod
from app.main import create_app

# ---------------------------------------------------------------------------
# Settings: everything enabled, no authentication, English UI.
# ---------------------------------------------------------------------------
settings = config_mod.Settings()
settings.host = "127.0.0.1"
settings.port = 8791
settings.login_required = False
settings.allow_unauthenticated = True
settings.basic_auth = False
settings.default_lang = "en"
settings.execution_mode = "local"
settings.modules = {
    key: config_mod.ModuleConfig(
        key=key,
        title=defaults.get("title", key),
        enabled=True,
        group=defaults.get("group", ""),
        url=defaults.get("url", ""),
        iframe=defaults.get("iframe", False),
        no_nav=defaults.get("no_nav", False),
    )
    for key, defaults in config_mod.DEFAULT_MODULES.items()
}
settings.dashboard_services = ["ssh", "nginx", "cron"]
# Keep generated data out of the real data/ directory.
SHOT_DATA = ROOT / ".screenshots" / "_data"
SHOT_DATA.mkdir(parents=True, exist_ok=True)
settings.data_dir = SHOT_DATA
settings.buttons_dir = SHOT_DATA / "buttons"
settings.command_logs_dir = SHOT_DATA / "command_logs"
settings.scripts_dir = SHOT_DATA / "user_scripts"
settings.backups_dir = SHOT_DATA / "backups"
settings.session_secret = "screenshot-only-not-a-real-secret"
settings.ensure_dirs()

app = create_app(settings)

# ---------------------------------------------------------------------------
# Force English regardless of browser Accept-Language.
# ---------------------------------------------------------------------------
from app import i18n as i18n_mod

_orig_current_lang = i18n_mod.current_lang


def _fake_current_lang(request, settings):
    return "en"


i18n_mod.current_lang = _fake_current_lang
# templating imported current_lang by name at module load:
from app import templating as templating_mod

templating_mod.current_lang = _fake_current_lang
# Suppress the "Authentication is disabled" banner in screenshots.
templating_mod.open_mode = lambda settings: False
templating_mod.default_credentials = lambda settings: False

# ---------------------------------------------------------------------------
# Dashboard: replace collect()/summary() with fictional readings.
# ---------------------------------------------------------------------------
from app.routers import dashboard as dash_router

FAKE_DASH = {
    "hostname": "pi-demo",
    "temp": 48.6,
    "cpu": [12.4, 9.8, 7.1, 8.2],
    "load": [0.42, 0.35, 0.28],
    "memory": {
        "total": 8589934592,
        "total_h": "8.0 GiB",
        "used": 2147483648,
        "used_h": "2.0 GiB",
        "available": 5917509632,
        "available_h": "5.5 GiB",
        "percent": 25.0,
        "cached_h": "1.1 GiB",
        "buffers_h": "212.0 MiB",
        "swap_total": 1073741824,
        "swap_total_h": "1.0 GiB",
        "swap_used": 0,
        "swap_used_h": "0 B",
        "swap_percent": 0,
    },
    "disk": {
        "total": 64000000000,
        "total_h": "59.6 GiB",
        "used": 22900000000,
        "used_h": "21.3 GiB",
        "available": 38000000000,
        "available_h": "35.4 GiB",
        "percent": 36,
    },
    "swap": {
        "total": 1073741824,
        "total_h": "1.0 GiB",
        "used": 0,
        "used_h": "0 B",
        "percent": 0,
    },
    "network": [
        {
            "name": "eth0",
            "ipv4": "192.168.1.50",
            "state": "up",
            "signal": "—",
            "rx_rate_h": "1.2 KB/s",
            "tx_rate_h": "842 B/s",
            "rx_h": "1.4 GiB",
            "tx_h": "312.6 MiB",
        },
        {
            "name": "wlan0",
            "ipv4": "192.168.1.87",
            "state": "up",
            "signal": "-51 dBm (72%)",
            "rx_rate_h": "3.8 KB/s",
            "tx_rate_h": "1.1 KB/s",
            "rx_h": "642.1 MiB",
            "tx_h": "88.3 MiB",
        },
    ],
    "throttled": {"available": True, "flags": [], "raw": "0x0"},
    "services": [
        {"name": "ssh.service", "state": "active"},
        {"name": "nginx.service", "state": "active"},
        {"name": "cron.service", "state": "active"},
    ],
    "info": {
        "hostname": "pi-demo",
        "model": "Raspberry Pi 5 Model B Rev 1.0",
        "os": "Debian GNU/Linux 12 (bookworm)",
        "kernel": "6.6.51+rpt-rpi-2712",
        "cpu": "ARM Cortex-A76 (4 cores)",
        "cores": "4",
        "uptime": "12 days, 4:37",
        "load": "0.42, 0.35, 0.28",
    },
    "clock": {
        "datetime": "2026-11-22 19:40:00",
        "timezone": "Asia/Shanghai (UTC+08:00)",
        "ntp_synced": True,
    },
    "processes": 186,
    "top": [
        {"pid": 1482, "user": "piweb", "cpu": 8.4, "mem": 6.2, "command": "/usr/bin/python3 /opt/piweb/.venv/bin/uvicorn app.main:app"},
        {"pid": 2210, "user": "www-data", "cpu": 1.1, "mem": 3.8, "command": "nginx: worker process"},
        {"pid": 930, "user": "root", "cpu": 0.6, "mem": 2.1, "command": "/usr/lib/systemd/systemd-journald"},
        {"pid": 3041, "user": "pi", "cpu": 0.3, "mem": 1.4, "command": "/usr/bin/dockerd -H fd://"},
        {"pid": 612, "user": "root", "cpu": 0.2, "mem": 1.1, "command": "/usr/sbin/cron -f"},
        {"pid": 1180, "user": "piweb", "cpu": 0.1, "mem": 0.9, "command": "/usr/bin/python3 -m alerts_worker"},
    ],
    "partitions": [
        {"mount": "/", "size": "59.6 GiB", "used": "21.3 GiB", "available": "35.4 GiB", "percent": "36%"},
        {"mount": "/boot/firmware", "size": "255.0 MiB", "used": "78.4 MiB", "available": "176.6 MiB", "percent": "31%"},
    ],
    "blocks": [
        {"name": "mmcblk0", "size": "59.6 GB", "model": "SD Card", "mounts": ["/", "/boot/firmware"], "type": "disk", "transport": "sd", "removable": False},
    ],
    "usb": ["USB 2.0 Hub — 1-1"],
    "users": [
        {"user": "pi", "tty": "tty1", "since": "2026-11-20 08:12", "from": ""},
        {"user": "pi", "tty": "ssh", "since": "2026-11-22 19:31", "from": "192.168.1.24"},
    ],
    "pinned": [],
    "docker": {"version": "27.3.1", "compose": "v2.30.3", "total": 5, "running": 4, "unhealthy": 0},
    "now": "2026-11-22 19:40:00",
}

FAKE_SUMMARY = {
    "temp": 48.6,
    "cpu": [12.4, 9.8, 7.1, 8.2],
    "load": [0.42, 0.35, 0.28],
    "cores": 4,
    "mem_used_h": "2.0 GiB",
    "mem_total_h": "8.0 GiB",
    "mem_percent": 25.0,
    "disk_used_h": "21.3 GiB",
    "disk_total_h": "59.6 GiB",
    "disk_percent": 36,
    "net": [
        {"name": "eth0", "rx": 1200.0, "tx": 840.0},
        {"name": "wlan0", "rx": 3800.0, "tx": 1100.0},
    ],
    "now": "19:40:00",
}

dash_router.collect = lambda *a, **k: FAKE_DASH
dash_router.summary = lambda *a, **k: FAKE_SUMMARY
dash_router.evaluate = lambda *a, **k: None
dash_router.evaluate_docker = lambda *a, **k: None


# Replace the infinite SSE stream with a finite one so headless Chrome's
# virtual clock can settle (an open EventSource hangs --screenshot).
from fastapi.responses import StreamingResponse
import json as _json


def _finite_events(request):
    def gen():
        for i in range(12):
            frame = dict(FAKE_SUMMARY)
            frame["temp"] = round(47.0 + i * 0.3, 1)
            frame["now"] = f"19:39:{40 + i:02d}"
            yield f"data: {_json.dumps(frame)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


for _route in app.routes:
    if getattr(_route, "path", "") == "/ajax/dashboard/events":
        _route.endpoint = _finite_events
        break

# ---------------------------------------------------------------------------
# Services: replace list_units()/list_unit_files().
# ---------------------------------------------------------------------------
from app.routers import services as svc_router

SVC_ROWS = [
    ("ssh.service", "ssh", "service", "active", "running", "OpenBSD Secure Shell server", "enabled"),
    ("nginx.service", "nginx", "service", "active", "running", "A high performance web server", "enabled"),
    ("cron.service", "cron", "service", "active", "running", "Regular background program processing daemon", "enabled"),
    ("docker.service", "docker", "service", "active", "running", "Docker Application Container Engine", "enabled"),
    ("piweb.service", "piweb", "service", "active", "running", "PiWeb control panel", "enabled"),
    ("systemd-journald.service", "systemd-journald", "service", "active", "running", "Journal Service", "static"),
    ("bluetooth.service", "bluetooth", "service", "inactive", "dead", "Bluetooth service", "enabled"),
    ("avahi-daemon.service", "avahi-daemon", "service", "active", "running", "Avahi mDNS/DNS-SD Stack", "enabled"),
    ("triggerhappy.service", "triggerhappy", "service", "failed", "failed", "triggerhappy global hotkey daemon", "enabled"),
    ("rsyslog.service", "rsyslog", "service", "active", "running", "System logging service", "enabled"),
    ("dbus.socket", "dbus", "socket", "active", "running", "D-Bus System Message Bus Socket", "static"),
    ("docker.socket", "docker", "socket", "active", "running", "Docker Socket for the API", "static"),
    ("apt-daily.timer", "apt-daily", "timer", "active", "waiting", "Daily apt download activities", "enabled"),
    ("logrotate.timer", "logrotate", "timer", "active", "waiting", "Daily rotation of log files", "enabled"),
    ("fstrim.timer", "fstrim", "timer", "inactive", "dead", "Discard unused blocks once a week", "disabled"),
]

_UNITS = [
    {"unit": u, "name": n, "type": t, "active": a, "sub": s, "description": d, "load": "loaded"}
    for (u, n, t, a, s, d, _fs) in SVC_ROWS
]
_FILES = {u: {"state": fs} for (u, n, t, a, s, d, fs) in SVC_ROWS}

svc_router.list_units = lambda executor, kinds=None: _UNITS
svc_router.list_unit_files = lambda executor, kinds=None: _FILES

# ---------------------------------------------------------------------------
# GPIO: replace gpio_state().
# ---------------------------------------------------------------------------
from app.routers import gpio as gpio_router


def _pin(bcm, name, mode="IN", v="0", pud="down"):
    return {"bcm": bcm, "name": name, "mode": mode, "v": v, "pud": pud}


GPIO_ROWS = [
    {"left": _pin("", "3V3"), "right": _pin("", "5V"), "physical": "1", "left_gpio": False, "right_gpio": False},
    {"left": _pin(2, "GPIO2", "IN", "1", "up"), "right": _pin("", "5V"), "physical": "2", "left_gpio": True, "right_gpio": False},
    {"left": _pin(3, "GPIO3", "IN", "1", "up"), "right": _pin("", "GND"), "physical": "3", "left_gpio": True, "right_gpio": False},
    {"left": _pin(4, "GPIO4", "IN", "0", "down"), "right": _pin(14, "GPIO14", "OUT", "0", "down"), "physical": "4", "left_gpio": True, "right_gpio": True},
    {"left": _pin("", "GND"), "right": _pin(15, "GPIO15", "OUT", "1", "down"), "physical": "5", "left_gpio": False, "right_gpio": True},
    {"left": _pin(17, "GPIO17", "OUT", "1", "down"), "right": _pin(18, "GPIO18", "IN", "0", "up"), "physical": "6", "left_gpio": True, "right_gpio": True},
    {"left": _pin(27, "GPIO27", "IN", "0", "down"), "right": _pin("", "GND"), "physical": "7", "left_gpio": True, "right_gpio": False},
    {"left": _pin(22, "GPIO22", "OUT", "0", "down"), "right": _pin(23, "GPIO23", "IN", "0", "down"), "physical": "8", "left_gpio": True, "right_gpio": True},
    {"left": _pin("", "3V3"), "right": _pin(24, "GPIO24", "IN", "1", "up"), "physical": "9", "left_gpio": False, "right_gpio": True},
    {"left": _pin(10, "GPIO10", "OUT", "0", "down"), "right": _pin("", "GND"), "physical": "10", "left_gpio": True, "right_gpio": False},
    {"left": _pin(9, "GPIO9", "OUT", "1", "down"), "right": _pin(25, "GPIO25", "IN", "0", "down"), "physical": "11", "left_gpio": True, "right_gpio": True},
    {"left": _pin(11, "GPIO11", "OUT", "0", "down"), "right": _pin(8, "GPIO8", "IN", "0", "down"), "physical": "12", "left_gpio": True, "right_gpio": True},
    {"left": _pin("", "GND"), "right": _pin(7, "GPIO7", "OUT", "1", "down"), "physical": "13", "left_gpio": False, "right_gpio": True},
    {"left": _pin(0, "GPIO0", "IN", "0", "down"), "right": _pin("", "GND"), "physical": "14", "left_gpio": True, "right_gpio": False},
    {"left": _pin(5, "GPIO5", "OUT", "1", "down"), "right": _pin(1, "GPIO1", "IN", "0", "down"), "physical": "15", "left_gpio": True, "right_gpio": True},
    {"left": _pin(6, "GPIO6", "OUT", "0", "down"), "right": _pin(12, "GPIO12", "IN", "1", "up"), "physical": "16", "left_gpio": True, "right_gpio": True},
    {"left": _pin("", "GND"), "right": _pin(13, "GPIO13", "OUT", "0", "down"), "physical": "17", "left_gpio": False, "right_gpio": True},
    {"left": _pin(19, "GPIO19", "IN", "0", "down"), "right": _pin("", "GND"), "physical": "18", "left_gpio": True, "right_gpio": False},
    {"left": _pin(16, "GPIO16", "OUT", "1", "down"), "right": _pin(20, "GPIO20", "IN", "0", "down"), "physical": "19", "left_gpio": True, "right_gpio": True},
    {"left": _pin(26, "GPIO26", "IN", "0", "down"), "right": _pin(21, "GPIO21", "OUT", "0", "down"), "physical": "20", "left_gpio": True, "right_gpio": True},
]

gpio_router.gpio_state = lambda executor: {
    "source": "wiringpi",
    "rows": GPIO_ROWS,
    "raw": "fake",
    "physical_col": 2,
}

# ---------------------------------------------------------------------------
# Docker: replace get_docker() with a fake client.
# ---------------------------------------------------------------------------
from app.routers import docker as docker_router
from app.docker import DockerState

CONTAINERS = [
    {
        "id": "a" * 64,
        "short_id": "aaaaaaa1111",
        "name": "cloudreve",
        "image": "ghcr.io/cloudreve/cloudreve:3.8.3",
        "command": "/tini -- /bin/sh -c start",
        "state": "running",
        "status": "Up 2 days (healthy)",
        "health": "healthy",
        "ports": "0.0.0.0:5212->5212/tcp",
        "created_at": "2026-11-18 10:00:00 +0800 CST",
        "running_for": "2 days ago",
        "project": "services",
        "service": "cloudreve",
        "networks": "services_default",
        "mounts": "/srv/cloudreve:/cloudreve/data",
        "labels": {"com.docker.compose.project": "services", "com.docker.compose.service": "cloudreve"},
    },
    {
        "id": "b" * 64,
        "short_id": "bbbbbbb2222",
        "name": "piweb",
        "image": "ghcr.io/xiejx/piweb:latest",
        "command": "uvicorn app.main:app --host 0.0.0.0",
        "state": "running",
        "status": "Up 6 hours",
        "health": "",
        "ports": "0.0.0.0:8000->8000/tcp",
        "created_at": "2026-11-20 09:00:00 +0800 CST",
        "running_for": "6 hours ago",
        "project": "services",
        "service": "piweb",
        "networks": "services_default",
        "mounts": "",
        "labels": {"com.docker.compose.project": "services", "com.docker.compose.service": "piweb"},
    },
    {
        "id": "c" * 64,
        "short_id": "ccccccc3333",
        "name": "mosquitto",
        "image": "eclipse-mosquitto:2.0.18",
        "command": "/usr/sbin/mosquitto -c /mosquitto/config/mosquitto.conf",
        "state": "running",
        "status": "Up 2 days",
        "health": "",
        "ports": "0.0.0.0:1883->1883/tcp, 0.0.0.0:9001->9001/tcp",
        "created_at": "2026-11-18 10:05:00 +0800 CST",
        "running_for": "2 days ago",
        "project": "services",
        "service": "mosquitto",
        "networks": "services_default",
        "mounts": "/srv/mosquitto:/mosquitto/config",
        "labels": {"com.docker.compose.project": "services", "com.docker.compose.service": "mosquitto"},
    },
    {
        "id": "d" * 64,
        "short_id": "ddddddd4444",
        "name": "portainer",
        "image": "portainer/portainer-ce:2.21.0",
        "command": "/portainer --host 0.0.0.0:9000",
        "state": "running",
        "status": "Up 12 days",
        "health": "",
        "ports": "0.0.0.0:9000->9000/tcp",
        "created_at": "2026-11-10 08:00:00 +0800 CST",
        "running_for": "12 days ago",
        "project": "tools",
        "service": "portainer",
        "networks": "tools_default",
        "mounts": "/var/run/docker.sock:/var/run/docker.sock",
        "labels": {"com.docker.compose.project": "tools", "com.docker.compose.service": "portainer"},
    },
    {
        "id": "e" * 64,
        "short_id": "eeeeeee5555",
        "name": "xunlei",
        "image": "xiejx/xunlei:debian-13",
        "command": "/xunlei/launch.sh",
        "state": "exited",
        "status": "Exited (137) 2 hours ago",
        "health": "",
        "ports": "",
        "created_at": "2026-11-17 09:00:00 +0800 CST",
        "running_for": "3 days ago",
        "project": "services",
        "service": "xunlei",
        "networks": "services_default",
        "mounts": "/srv/xunlei:/downloads",
        "labels": {"com.docker.compose.project": "services", "com.docker.compose.service": "xunlei"},
    },
]

IMAGES = [
    {"id": "sha256:aaaa", "repository": "ghcr.io/cloudreve/cloudreve", "tag": "3.8.3", "size": "612MB", "created_since": "2 days ago", "containers": 1},
    {"id": "sha256:bbbb", "repository": "ghcr.io/xiejx/piweb", "tag": "latest", "size": "284MB", "created_since": "3 days ago", "containers": 1},
    {"id": "sha256:cccc", "repository": "eclipse-mosquitto", "tag": "2.0.18", "size": "18.4MB", "created_since": "2 weeks ago", "containers": 1},
    {"id": "sha256:dddd", "repository": "portainer/portainer-ce", "tag": "2.21.0", "size": "341MB", "created_since": "2 weeks ago", "containers": 1},
    {"id": "sha256:eeee", "repository": "xiejx/xunlei", "tag": "debian-13", "size": "428MB", "created_since": "5 days ago", "containers": 1},
]

VOLUMES = [
    {"name": "services_cloudreve-data", "driver": "local", "mountpoint": "/var/lib/docker/volumes/services_cloudreve-data/_data", "size": "1.2GB"},
    {"name": "tools_portainer-data", "driver": "local", "mountpoint": "/var/lib/docker/volumes/tools_portainer-data/_data", "size": "12.4MB"},
]

NETWORKS = [
    {"name": "services_default", "id": "aaaa", "driver": "bridge", "scope": "local", "subnet": "172.20.0.0/16", "containers": 4},
    {"name": "tools_default", "id": "bbbb", "driver": "bridge", "scope": "local", "subnet": "172.21.0.0/16", "containers": 1},
    {"name": "bridge", "id": "cccc", "driver": "bridge", "scope": "local", "subnet": "172.17.0.0/16", "containers": 0},
]

COMPOSE = [
    {"name": "services", "services": ["cloudreve", "piweb", "mosquitto", "xunlei"], "status": "running", "config_file": "/srv/services/docker-compose.yml"},
    {"name": "tools", "services": ["portainer"], "status": "running", "config_file": "/srv/tools/docker-compose.yml"},
]

DF_ROWS = [
    {"type": "Images", "total_count": 5, "active": 5, "size": "1.68GB", "reclaimable": "0B"},
    {"type": "Containers", "total_count": 5, "active": 4, "size": "32.8kB", "reclaimable": "0B"},
    {"type": "Build Cache", "total_count": 12, "active": 0, "size": "488.2MB", "reclaimable": "488.2MB (100%)"},
]

FS = DockerState(available=True, version="27.3.1", compose="v2.30.3", privileged="group")


class FakeDocker:
    def state(self, refresh=False):
        return FS

    def counts(self):
        return {"total": 5, "running": 4, "unhealthy": 0, "exited": 1, "paused": 0}

    def containers(self, all_containers=True, refresh=False):
        return CONTAINERS if all_containers else [c for c in CONTAINERS if c["state"] == "running"]

    def images(self, all_images=False, refresh=False):
        return IMAGES

    def volumes(self, refresh=False):
        return VOLUMES

    def networks(self, refresh=False):
        return NETWORKS

    def compose_projects(self, refresh=False):
        return COMPOSE

    def df(self):
        return {"rows": DF_ROWS, "ok": True}

    def info(self):
        return {
            "ok": True,
            "version": "27.3.1",
            "api_version": "1.47",
            "os": "linux",
            "arch": "aarch64",
            "kernel": "6.6.51+rpt-rpi-2712",
            "cpus": 4,
            "memory": "8.0GiB",
            "docker_root_dir": "/var/lib/docker",
            "storage_driver": "overlay2",
            "logging_driver": "json-file",
            "compose": "v2.30.3",
        }

    def events(self, minutes=60, limit=80):
        return [
            {"time": "19:38:12", "action": "start", "type": "container", "name": "piweb"},
            {"time": "19:21:04", "action": "die", "type": "container", "name": "xunlei"},
            {"time": "18:02:55", "action": "create", "type": "container", "name": "portainer"},
        ]

    def inspect(self, ref):
        c = next((x for x in CONTAINERS if x["id"].startswith(ref) or x["name"] == ref), CONTAINERS[0])
        return {
            "ok": True,
            "id": c["id"],
            "name": c["name"],
            "image": c["image"],
            "state": {"status": c["state"], "health": c["health"], "running": c["state"] == "running", "exit_code": 0 if c["state"] == "running" else 137, "started_at": c["created_at"], "finished_at": ""},
            "restart_policy": "unless-stopped",
            "ports": c["ports"],
            "networks": c["networks"],
            "mounts": c["mounts"],
            "env": ["PATH=/usr/local/bin:/usr/bin", "TZ=Asia/Shanghai", "PUID=1000", "PGID=1000"],
        }

    def logs(self, ref, lines=300, since="", timestamps=False):
        return {"ok": True, "output": "[19:40:01] INFO  server listening on 0.0.0.0:5212\n[19:39:58] INFO  request handled in 12ms\n[19:39:44] INFO  health check passed"}

    def stats(self, ref):
        return {"ok": True, "supported": True, "cpu": "2.4%", "mem": "184MiB / 8GiB", "mem_percent": "2.2%", "net_io": "1.2MB / 842kB"}

    def network_inspect(self, name):
        return {"ok": True, "network": {"name": name, "id": "aaaa", "driver": "bridge", "scope": "local", "subnet": "172.20.0.0/16", "gateway": "172.20.0.1", "containers": {"cloudreve": "172.20.0.2", "piweb": "172.20.0.3"}}}

    def compose_file(self, project):
        return {"ok": True, "path": "/srv/services/docker-compose.yml", "content": "services:\n  piweb:\n    image: ghcr.io/xiejx/piweb:latest\n    ports:\n      - '8000:8000'"}

    def task_status(self, task_id):
        return {"ok": True, "status": "done"}

    def prune_preview(self, target):
        return {"ok": True, "size": "488.2MB", "count": 12}


_FAKE_DOCKER = FakeDocker()
docker_router.get_docker = lambda *a, **k: _FAKE_DOCKER

# Dashboard's docker_overview uses app.docker.get_docker; already patched via
# FAKE_DASH, so no need to touch it.

# ---------------------------------------------------------------------------
# Serve.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    print("Fake-data PiWeb listening on http://127.0.0.1:8791")
    uvicorn.run(app, host="127.0.0.1", port=8791, log_level="warning")
