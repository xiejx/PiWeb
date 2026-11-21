"""Tests for the Services page, which now lists several systemd unit types.

The page used to show `.service` units only. It now asks systemctl for every
supported type in a single call and renders one card per type, so these tests
pin the grouping, the state/search filters and the crash-free rendering.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.executor import CommandResult
from app.main import create_app
from app.routers import services as services_router

# Shaped like real `systemctl list-units --all --plain` output, including the
# state bullets of older systemd builds and the trailing summary line.
LIST_UNITS = """\
  ssh.service                     loaded    active   running OpenBSD Secure Shell server
  cron.service                    loaded    active   running Regular background program processing daemon
● broken.service                  loaded    failed   failed  Broken helper
○ missing.service                 not-found inactive dead    missing.service
  dbus.socket                     loaded    active   running D-Bus System Message Bus Socket
  avahi-daemon.socket             loaded    active   running Avahi mDNS/DNS-SD Stack Activation Socket
  systemd-tmpfiles-clean.timer    loaded    active   waiting Daily Cleanup of Temporary Directories
  fstrim.timer                    loaded    inactive dead    Discard unused blocks once a week
  -.mount                         loaded    active   mounted Root Mount
  boot.mount                      loaded    active   mounted /boot
  media-pi-usb.automount          loaded    active   waiting Automatically mounted USB stick
  dev-disk-by\\x2duuid-abcd.swap   loaded    active   active  /dev/disk/by-uuid/abcd
  multi-user.target               loaded    active   active  Multi-User System
  paths.target                    loaded    active   active  Paths
14 loaded units listed.
"""

LIST_UNIT_FILES = """\
ssh.service                     enabled   enabled
cron.service                    enabled   enabled
dbus.socket                     static    -
avahi-daemon.socket             enabled   enabled
systemd-tmpfiles-clean.timer    static    -
fstrim.timer                    enabled   enabled
-.mount                         generated -
boot.mount                      generated -
media-pi-usb.automount          generated -
dev-disk-by\\x2duuid-abcd.swap   generated -
multi-user.target               static    -
paths.target                    static    -
14 unit files listed.
"""


class SystemctlStub:
    """Answers `systemctl list-units` / `list-unit-files` from canned text."""

    def __init__(self, units: str = LIST_UNITS, unit_files: str = LIST_UNIT_FILES) -> None:
        self.units = units
        self.unit_files = unit_files
        self.commands: list[str] = []

    def run(
        self,
        command: str,
        timeout: float = 30.0,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        input: str | None = None,
    ) -> CommandResult:
        self.commands.append(command)
        if "list-unit-files" in command:
            return CommandResult(stdout=self.unit_files, returncode=0)
        if "list-units" in command:
            return CommandResult(stdout=self.units, returncode=0)
        return CommandResult(stdout="", returncode=1)

    def run_argv(self, argv, timeout: float = 30.0, input: str | None = None) -> CommandResult:
        self.commands.append(" ".join(str(part) for part in argv))
        if "status" in argv:
            return CommandResult(stdout="● ssh.service - OpenBSD Secure Shell server\n   Active: active (running)", returncode=0)
        if "journalctl" in argv:
            return CommandResult(stdout="Nov 21 10:00:00 pi sshd[1]: Accepted publickey for pi", returncode=0)
        return CommandResult(stdout="", returncode=1)


def section_map(view: dict) -> dict[str, dict]:
    return {section["key"]: section for section in view["sections"]}


# ── grouping ─────────────────────────────────────────────────────────────────


def test_sections_are_grouped_by_unit_type():
    view = services_router.service_view(SystemctlStub())
    # Canonical order, and `path` is dropped because this host has no path unit.
    assert [section["key"] for section in view["sections"]] == [
        "service",
        "socket",
        "timer",
        "mount",
        "automount",
        "swap",
        "target",
    ]
    sections = section_map(view)
    # Failures first, then running units alphabetically, then idle ones.
    assert [row["name"] for row in sections["service"]["rows"]] == [
        "broken",
        "cron",
        "ssh",
        "missing",
    ]
    assert [row["name"] for row in sections["socket"]["rows"]] == [
        "avahi-daemon",
        "dbus",
    ]
    assert sections["timer"]["total"] == 2
    assert sections["mount"]["total"] == 2
    assert view["total"] == 14


def test_one_systemctl_call_per_command_with_repeated_type_flags():
    stub = SystemctlStub()
    services_router.service_view(stub)
    assert len(stub.commands) == 2
    list_units_cmd = next(cmd for cmd in stub.commands if "list-units" in cmd)
    assert list_units_cmd.count("--type=") == 8
    assert "--type=socket" in list_units_cmd and "--type=timer" in list_units_cmd


def test_trailer_line_is_not_parsed_as_a_unit():
    view = services_router.service_view(SystemctlStub())
    names = [row["name"] for section in view["sections"] for row in section["rows"]]
    assert "14" not in names


def test_unit_basename_drops_the_type_suffix():
    view = services_router.service_view(SystemctlStub())
    mounts = section_map(view)["mount"]["rows"]
    assert [row["unit"] for row in mounts] == ["-.mount", "boot.mount"]
    assert [row["name"] for row in mounts] == ["-", "boot"]


def test_boot_state_comes_from_list_unit_files():
    view = services_router.service_view(SystemctlStub())
    sections = section_map(view)
    ssh = next(row for row in sections["service"]["rows"] if row["name"] == "ssh")
    dbus = next(row for row in sections["socket"]["rows"] if row["name"] == "dbus")
    assert ssh["file_state"] == "enabled"
    assert dbus["file_state"] == "static"


# ── filters ──────────────────────────────────────────────────────────────────


def test_state_filter_applies_to_every_section():
    view = services_router.service_view(SystemctlStub(), state="failed")
    sections = section_map(view)
    assert list(sections) == ["service"]
    assert [row["name"] for row in sections["service"]["rows"]] == ["broken"]
    assert sections["service"]["total"] == 4
    # Counts stay global so the filter chips keep describing the whole host.
    assert view["counts"]["failed"] == 1


def test_search_terms_are_anded_across_name_and_description():
    # "avahi" is the unit name, "mDNS" only appears in the description — one row
    # satisfies both terms.
    view = services_router.service_view(SystemctlStub(), q="avahi mDNS")
    sections = section_map(view)
    assert list(sections) == ["socket"]
    assert [row["name"] for row in sections["socket"]["rows"]] == ["avahi-daemon"]

    # Two terms that no single unit carries must match nothing.
    empty = services_router.service_view(SystemctlStub(), q="avahi dbus")
    assert empty["sections"] == []
    assert empty["total"] == 14


def test_unknown_state_falls_back_to_all():
    view = services_router.service_view(SystemctlStub(), state="bogus")
    assert view["state"] == "all"
    assert view["total"] == 14


# ── HTTP surface ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        allow_unauthenticated=True,
        default_lang="en",
        session_secret="test-secret",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "command_logs",
        scripts_dir=tmp_path / "user_scripts",
        backups_dir=tmp_path / "backups",
    )
    settings.ensure_dirs()
    stub = SystemctlStub()
    monkeypatch.setattr(services_router, "get_executor", lambda _settings: stub)
    with TestClient(create_app(settings)) as test_client:
        test_client.stub = stub
        yield test_client


def test_services_page_renders_one_card_per_type(client: TestClient):
    response = client.get("/services")
    assert response.status_code == 200
    body = response.text
    assert "Sockets" in body
    assert "Timers" in body
    # Actions must carry the full unit name, otherwise systemctl resolves a bare
    # "dbus" to dbus.service and acts on the wrong unit.
    assert 'value="dbus.socket"' in body
    assert 'value="dbus"' not in body
    assert 'value="fstrim.timer"' in body
    # No path units in the fixture, so that card must not be rendered.
    assert "Path units" not in body


def test_services_partial_renders_sections(client: TestClient):
    response = client.get("/partials/services", params={"state": "active"})
    assert response.status_code == 200
    assert "Timers" in response.text


def test_unit_tables_have_a_scroll_cap(client: TestClient):
    """A busy unit type must not push the following cards far down the page."""
    body = client.get("/services").text
    # Every type card wraps its table in the capped, vertically scrollable box.
    assert body.count("table-responsive svc-table-scroll") == 7

    css = (
        Path(__file__).resolve().parents[1]
        / "app" / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")
    assert ".svc-table-scroll" in css
    assert "max-height" in css
    assert "overflow-y: auto" in css
    # The header stays put while a long table is scrolled.
    assert ".svc-table-scroll thead th" in css
    assert "position: sticky" in css


def test_status_endpoint_returns_systemctl_output(client: TestClient):
    data = client.get("/ajax/services/status", params={"unit": "ssh.service"}).json()
    assert data["ok"] is True
    assert "ssh.service" in data["output"]
    assert "Active: active (running)" in data["output"]
    assert any(cmd.startswith("systemctl status ssh.service") for cmd in client.stub.commands)


def test_logs_endpoint_returns_journal_output(client: TestClient):
    data = client.get("/ajax/services/logs", params={"unit": "ssh.service", "lines": 500}).json()
    assert data["ok"] is True
    assert "sshd[1]" in data["output"]
    assert any(
        cmd.startswith("journalctl -u ssh.service -n 500") for cmd in client.stub.commands
    )


def test_detail_endpoints_reject_bad_unit_names(client: TestClient):
    for path in ("/ajax/services/status", "/ajax/services/logs"):
        response = client.get(path, params={"unit": "ssh; rm -rf /"})
        assert response.status_code == 400
        assert response.json()["ok"] is False


def test_logs_endpoint_clamps_line_count(client: TestClient):
    client.get("/ajax/services/logs", params={"unit": "cron.service", "lines": 99999})
    assert any("-n 200" in cmd for cmd in client.stub.commands)


def test_page_wires_status_and_logs_buttons(client: TestClient):
    body = client.get("/services").text
    assert 'data-kind="status"' in body
    assert 'data-kind="logs"' in body
    assert 'id="svcDetailModal"' in body
    assert "/ajax/services/status?unit=" in body
    assert "/ajax/services/logs?unit=" in body


def test_action_runs_systemctl_against_the_full_unit_name(client: TestClient):
    token = re.search(
        r'name="csrf-token" content="([^"]+)"', client.get("/services").text
    ).group(1)
    response = client.post(
        "/services/action",
        data={
            "sname": "dbus.socket",
            "action": "stop",
            "q": "",
            "state": "all",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "sudo systemctl stop dbus.socket" in client.stub.commands
