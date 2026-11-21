"""Tests for the Docker module.

The module parses `--format '{{json .}}'` output and builds argument lists, so
these tests pin the parsing, the validation and the guarantees that keep weird
container names from turning into shell commands. No Docker daemon is needed:
the executor is stubbed.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import docker as docker_api
from app.config import DockerConfig, ModuleConfig, Settings
from app.docker import Docker, prune_argv, valid_image_ref, valid_since
from app.executor import CommandResult
from app.main import create_app

PS_JSON = "\n".join(
    [
        json.dumps(
            {
                "Command": '"/tini -- /bin/sh -c start"',
                "CreatedAt": "2026-11-18 10:00:00 +0800 CST",
                "ID": "a" * 64,
                "Image": "xavierniu/cloudreve:3.5.1",
                "Labels": (
                    "com.docker.compose.project=services,"
                    "com.docker.compose.service=cloudreve,"
                    "com.docker.compose.project.working_dir=/home/pi/services,"
                    "com.docker.compose.project.config_files=/home/pi/services/docker-compose.yml"
                ),
                "Names": "cloudreve",
                "Networks": "services_default",
                "Ports": "0.0.0.0:5212->5212/tcp",
                "RunningFor": "2 days ago",
                "State": "running",
                "Status": "Up 2 days (healthy)",
            }
        ),
        json.dumps(
            {
                "Command": '"/xunlei/launch.sh"',
                "CreatedAt": "2026-11-17 09:00:00 +0800 CST",
                "ID": "b" * 64,
                "Image": "xiejx/xunlei:debian-13-1.0.0.1",
                "Labels": "com.docker.compose.project=services,com.docker.compose.service=xunlei",
                "Names": "xunlei",
                "Networks": "services_default",
                "Ports": "",
                "RunningFor": "3 days ago",
                "State": "exited",
                "Status": "Exited (137) 2 hours ago",
            }
        ),
    ]
)

IMAGES_JSON = "\n".join(
    [
        json.dumps(
            {
                "Containers": 2,
                "CreatedSince": "2 days ago",
                "Digest": "<none>",
                "ID": "sha256:" + "c" * 64,
                "Repository": "xavierniu/cloudreve",
                "SharedSize": "0B",
                "Size": "612MB",
                "Tag": "3.5.1",
                "UniqueSize": "612MB",
            }
        ),
        json.dumps(
            {
                "Containers": 0,
                "CreatedSince": "3 weeks ago",
                "Digest": "<none>",
                "ID": "sha256:" + "d" * 64,
                "Repository": "<none>",
                "SharedSize": "0B",
                "Size": "120MB",
                "Tag": "<none>",
                "UniqueSize": "120MB",
            }
        ),
    ]
)

DF_JSON = "\n".join(
    [
        json.dumps({"Active": 2, "Reclaimable": "6.815kB (0%)", "Size": "840MB", "TotalCount": 3, "Type": "Images"}),
        json.dumps({"Active": 2, "Reclaimable": "0B", "Size": "32.77kB", "TotalCount": 2, "Type": "Containers"}),
        json.dumps({"Active": 0, "Reclaimable": "183.5MB", "Size": "622.4MB", "TotalCount": 11, "Type": "Build Cache"}),
    ]
)

NETWORK_INSPECT_JSON = json.dumps(
    {
        "Name": "services_default",
        "Id": "e" * 64,
        "Created": "2026-11-18T10:00:00Z",
        "Scope": "local",
        "Driver": "bridge",
        "EnableIPv6": False,
        "IPAM": {
            "Driver": "default",
            "Config": [
                {
                    "Subnet": "172.20.0.0/16",
                    "Gateway": "172.20.0.1",
                    "AuxiliaryAddresses": {"host1": "172.20.0.5"},
                }
            ],
        },
        "Internal": False,
        "Attachable": True,
        "Ingress": False,
        "Containers": {
            "a" * 64: {
                "Name": "cloudreve",
                "EndpointID": "ep-1",
                "MacAddress": "02:42:ac:14:00:02",
                "IPv4Address": "172.20.0.2/16",
                "IPv6Address": "",
            }
        },
        "Options": {"com.docker.network.bridge.name": "br-services"},
        "Labels": {"com.docker.compose.project": "services"},
    }
)

INFO_JSON = json.dumps(
    {
        "ServerVersion": "29.7.2",
        "Driver": "overlayfs",
        "CgroupVersion": "2",
        "DockerRootDir": "/var/lib/docker",
        "OperatingSystem": "Debian GNU/Linux 13 (trixie)",
        "Architecture": "aarch64",
        "KernelVersion": "6.12.0-rpt-rpi",
        "NCPU": 4,
        "MemTotal": 8_000_000_000,
        "Containers": 2,
        "ContainersRunning": 2,
        "ContainersPaused": 0,
        "ContainersStopped": 0,
        "Images": 3,
        "Swarm": {"LocalNodeState": "inactive"},
    }
)


class Stub:
    """Answers docker CLI calls from canned text and records the argv used."""

    def __init__(self, version: str = "29.7.2", compose: str = "v5.5.0", reason: str = ""):
        self.version = version
        self.compose = compose
        self.reason = reason  # "", "missing", "daemon", "permission"
        self.commands: list[list[str]] = []
        self.background: list[str] = []

    def run_argv(self, argv, timeout: float = 30.0, input: str | None = None) -> CommandResult:
        args = [str(part) for part in argv]
        self.commands.append(args)
        sub = args[1:]
        head = sub[0] if sub else ""

        if head == "version":
            if self.reason == "missing":
                return CommandResult(returncode=127, stderr="docker: command not found")
            if self.reason == "daemon":
                return CommandResult(
                    returncode=1,
                    stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock.",
                )
            if self.reason == "permission":
                return CommandResult(
                    returncode=1,
                    stderr="permission denied while trying to connect to the Docker daemon socket",
                )
            return CommandResult(stdout=self.version + "\n")
        if head == "compose" and "version" in sub:
            if not self.compose:
                return CommandResult(returncode=1, stderr="docker: 'compose' is not a docker command")
            return CommandResult(stdout=self.compose + "\n")
        if head == "ps":
            return CommandResult(stdout=PS_JSON)
        if head == "images":
            return CommandResult(stdout=IMAGES_JSON)
        if head == "system" and "df" in sub:
            return CommandResult(stdout=DF_JSON)
        if head == "info":
            return CommandResult(stdout=INFO_JSON)
        if head == "stats":
            return CommandResult(
                stdout=json.dumps(
                    {
                        "CPUPerc": "0.00%",
                        "MemUsage": "0B / 0B",
                        "MemPerc": "0.00%",
                        "NetIO": "0B / 0B",
                        "PIDs": 7,
                    }
                )
                + "\n"
            )
        if head == "logs":
            return CommandResult(stdout="line one\nline two\n")
        if head == "inspect":
            return CommandResult(
                stdout=json.dumps(
                    {
                        "Id": "a" * 64,
                        "Name": "/cloudreve",
                        "Created": "2026-11-18T02:00:00Z",
                        "Platform": "linux",
                        "Config": {
                            "Image": "xavierniu/cloudreve:3.5.1",
                            "Env": ["PATH=/usr/bin", "DB_PASSWORD=hunter2"],
                            "Labels": {"com.docker.compose.project": "services"},
                            "Cmd": ["/cloudreve"],
                        },
                        "HostConfig": {"RestartPolicy": {"Name": "unless-stopped"}},
                        "State": {
                            "Status": "running",
                            "Running": True,
                            "ExitCode": 0,
                            "StartedAt": "2026-11-18T02:00:01Z",
                        },
                        "Mounts": [
                            {"Type": "volume", "Source": "/var/lib/docker/volumes/x", "Destination": "/data", "RW": True}
                        ],
                        "NetworkSettings": {"Networks": {"services_default": {"IPAddress": "172.18.0.2"}}},
                    }
                )
            )
        if head in {"start", "stop", "restart", "pause", "unpause", "kill", "rename", "rm", "tag"}:
            return CommandResult(stdout="")
        if head == "network" and "inspect" in sub:
            return CommandResult(stdout=NETWORK_INSPECT_JSON)
        if head in {"volume", "network"}:
            return CommandResult(stdout="")
        return CommandResult(returncode=1, stderr="unsupported in stub")

    def run_background(self, command: str, log_path: Path) -> CommandResult:
        self.background.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("Pulling...\n__PIWEB_TASK_DONE__ rc=0\n", encoding="utf-8")
        return CommandResult(stdout="started")


def make_settings(tmp_path: Path, docker_enabled: bool = True, **docker_kwargs) -> Settings:
    settings = Settings(
        allow_unauthenticated=True,
        session_secret="test-secret",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "command_logs",
        scripts_dir=tmp_path / "user_scripts",
        backups_dir=tmp_path / "backups",
        docker=DockerConfig(**docker_kwargs),
    )
    settings.modules = {
        "docker": ModuleConfig(key="docker", title="Docker", enabled=docker_enabled)
    }
    settings.ensure_dirs()
    return settings


@pytest.fixture(autouse=True)
def clear_cache():
    docker_api.invalidate()
    yield
    docker_api.invalidate()


# ── parsing ──────────────────────────────────────────────────────────────────


def test_containers_are_parsed_from_json_lines(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    rows = client.containers()

    assert [row["name"] for row in rows] == ["cloudreve", "xunlei"]
    cloudreve = rows[0]
    assert cloudreve["image"] == "xavierniu/cloudreve:3.5.1"
    assert cloudreve["health"] == "healthy"
    assert cloudreve["project"] == "services"
    assert cloudreve["service"] == "cloudreve"
    assert cloudreve["short_id"] == "a" * 12
    assert cloudreve["ports"] == "0.0.0.0:5212->5212/tcp"


def test_exited_container_has_no_health_and_keeps_its_status(tmp_path: Path):
    rows = Docker(make_settings(tmp_path), Stub()).containers()
    xunlei = next(row for row in rows if row["name"] == "xunlei")
    assert xunlei["state"] == "exited"
    assert xunlei["health"] == ""
    assert xunlei["status"].startswith("Exited (137)")


def test_images_keep_dangling_entries_and_human_size(tmp_path: Path):
    images = Docker(make_settings(tmp_path), Stub()).images()
    assert [image["ref"] for image in images] == ["xavierniu/cloudreve:3.5.1", "<none>"]
    assert images[0]["size"] == "612MB"
    assert images[1]["dangling"] is True


def test_df_and_info_are_curated(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    rows = client.df()["rows"]
    assert [row["type"] for row in rows] == ["Images", "Containers", "Build Cache"]
    assert rows[2]["reclaimable"] == "183.5MB"

    info = client.info()
    assert info["server_version"] == "29.7.2"
    assert info["cgroup_version"] == "2"
    assert info["swarm"] == "inactive"


def test_inspect_masks_sensitive_environment(tmp_path: Path):
    data = Docker(make_settings(tmp_path), Stub()).inspect("cloudreve")
    assert data["ok"] is True
    assert "DB_PASSWORD=***" in data["config"]["env"]
    assert "PATH=/usr/bin" in data["config"]["env"]
    assert data["restart_policy"] == "unless-stopped"
    assert data["networks"][0]["ip"] == "172.18.0.2"


def test_stats_report_unsupported_when_memory_is_zero(tmp_path: Path):
    data = Docker(make_settings(tmp_path), Stub()).stats("cloudreve")
    assert data["ok"] is True
    assert data["available"] is False
    assert data["reason"] == "unsupported"


# ── engine availability ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("reason", "expected"),
    [("missing", "missing"), ("daemon", "daemon"), ("permission", "permission")],
)
def test_state_maps_stderr_to_a_reason(tmp_path: Path, reason: str, expected: str):
    state = Docker(make_settings(tmp_path), Stub(reason=reason)).state()
    assert state.available is False
    assert state.reason == expected


def test_state_reports_version_and_compose_when_healthy(tmp_path: Path):
    state = Docker(make_settings(tmp_path), Stub()).state()
    assert state.available is True
    assert state.version == "29.7.2"
    assert state.compose == "v5.5.0"


def test_state_cache_can_be_invalidated(tmp_path: Path):
    settings = make_settings(tmp_path)
    ok = Stub()
    assert Docker(settings, ok).state().available is True
    docker_api.invalidate()
    assert Docker(settings, Stub(reason="daemon")).state().reason == "daemon"


# ── commands ─────────────────────────────────────────────────────────────────


def test_commands_are_argument_lists_not_strings(tmp_path: Path):
    stub = Stub()
    Docker(make_settings(tmp_path), stub).run("stop", "cloudreve")
    assert stub.commands[-1] == ["docker", "stop", "cloudreve"]


def test_sudo_mode_prefixes_the_command(tmp_path: Path):
    stub = Stub()
    Docker(make_settings(tmp_path, privileged="sudo"), stub).run("ps")
    assert stub.commands[-1][:2] == ["sudo", "docker"]


def test_logs_clamp_the_line_count_and_reject_bad_since(tmp_path: Path):
    stub = Stub()
    client = Docker(make_settings(tmp_path, log_max_lines=2000), stub)
    client.logs("cloudreve", lines=99999)
    assert stub.commands[-1][-2:] == ["cloudreve"][-2:] or "cloudreve" in stub.commands[-1]
    assert "2000" in stub.commands[-1]

    client.logs("cloudreve", lines=1, since="2h")
    assert "--since" in stub.commands[-1]

    # An invalid value is dropped instead of being passed to the shell.
    client.logs("cloudreve", lines=10, since="; rm -rf /")
    assert "--since" not in stub.commands[-1]


def test_network_inspect_curates_ipam_and_containers(tmp_path: Path):
    stub = Stub()
    data = Docker(make_settings(tmp_path), stub).network_inspect("services_default")
    assert stub.commands[-1] == [
        "docker", "network", "inspect", "--format", "{{json .}}", "services_default"
    ]
    assert data["ok"] is True
    assert data["name"] == "services_default"
    assert data["driver"] == "bridge"
    assert data["scope"] == "local"
    assert data["attachable"] is True
    assert data["ipv6"] is False
    assert data["ipam_driver"] == "default"
    assert data["subnets"][0]["subnet"] == "172.20.0.0/16"
    assert data["subnets"][0]["gateway"] == "172.20.0.1"
    assert data["subnets"][0]["aux_addresses"] == {"host1": "172.20.0.5"}
    assert data["containers"][0]["name"] == "cloudreve"
    assert data["containers"][0]["ipv4"] == "172.20.0.2/16"
    assert data["options"]["com.docker.network.bridge.name"] == "br-services"
    assert data["labels"]["com.docker.compose.project"] == "services"


def test_network_inspect_reports_bad_output(tmp_path: Path):
    class BrokenStub(Stub):
        def run_argv(self, argv, timeout: float = 30.0, input: str | None = None):
            if argv[1:2] == ["network"] and "inspect" in argv:
                return CommandResult(stdout="not json")
            return super().run_argv(argv, timeout, input)

    data = Docker(make_settings(tmp_path), BrokenStub()).network_inspect("x")
    assert data["ok"] is False
    assert data["error"]


def test_prune_argv_combinations(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    argv, error = prune_argv(client, ["buildcache"])
    assert error == "" and argv[-3:] == ["builder", "prune", "-f"]

    argv, error = prune_argv(client, ["containers", "images", "networks", "buildcache"])
    assert error == "" and "system" in argv

    argv, error = prune_argv(client, ["volumes"])
    assert error == "" and "volume" in argv

    argv, error = prune_argv(client, ["images", "volumes"])
    assert argv == [] and error


# ── compose ──────────────────────────────────────────────────────────────────


def test_compose_projects_group_containers_by_label(tmp_path: Path):
    projects = Docker(make_settings(tmp_path), Stub()).compose_projects()
    assert len(projects) == 1
    project = projects[0]
    assert project["name"] == "services"
    assert project["total"] == 2
    assert project["running"] == 1
    assert sorted(service["name"] for service in project["services"]) == ["cloudreve", "xunlei"]
    assert project["dir"] == "/home/pi/services"


def test_compose_up_uses_recorded_directory_and_project(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    args, error = client.compose_argv("services", "up")
    assert error == ""
    assert args[:2] == ["compose", "--project-directory"]
    assert "/home/pi/services/docker-compose.yml" in args
    assert args[-2:] == ["up", "-d"]
    assert "-p" in args and "services" in args


def test_compose_down_can_remove_volumes(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    args, _ = client.compose_argv("services", "down", remove_volumes=True)
    assert args[-2:] == ["down", "--volumes"]


def test_compose_argv_targets_single_service(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    up, error = client.compose_argv("services", "up", service="cloudreve")
    assert error == ""
    assert up[-3:] == ["up", "-d", "cloudreve"]
    down, error = client.compose_argv("services", "down", service="xunlei")
    assert error == ""
    assert down[-2:] == ["down", "xunlei"]


def test_compose_argv_rejects_unknown_or_misused_service(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    assert client.compose_argv("services", "up", service="nosuch")[1] == "Unknown service in project"
    assert client.compose_argv("services", "up", service="bad name")[1] == "Invalid service name"
    assert client.compose_argv("services", "restart", service="xunlei")[1] == (
        "Service target is only supported for up and down"
    )


def test_compose_rejects_paths_outside_the_whitelist(tmp_path: Path):
    client = Docker(make_settings(tmp_path, compose_dirs=["/srv/stacks"]), Stub())
    args, error = client.compose_argv("services", "up")
    assert args == []
    assert "outside" in error


def test_compose_file_is_read_through_the_executor(tmp_path: Path):
    class FileStub(Stub):
        def run_argv(self, argv, timeout: float = 30.0, input: str | None = None):
            if argv[0] == "cat":
                return CommandResult(stdout="services:\n  cloudreve:\n    image: xavierniu/cloudreve\n")
            return super().run_argv(argv, timeout, input)

    data = Docker(make_settings(tmp_path), FileStub()).compose_file("services")
    assert data["ok"] is True
    assert "cloudreve" in data["content"]


def test_compose_file_marks_permission_denied(tmp_path: Path):
    """A 0700 home directory blocks traversal even when the file is readable."""

    class DeniedStub(Stub):
        def run_argv(self, argv, timeout: float = 30.0, input: str | None = None):
            if argv[0] == "cat":
                return CommandResult(
                    returncode=1,
                    stderr="cat: /home/pi/services/docker-compose.yml: Permission denied",
                )
            return super().run_argv(argv, timeout, input)

    data = Docker(make_settings(tmp_path), DeniedStub()).compose_file("services")
    assert data["ok"] is False
    assert data["reason"] == "denied"
    assert data["path"] == "/home/pi/services/docker-compose.yml"


def test_compose_argv_refuses_unreadable_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client = Docker(make_settings(tmp_path), Stub())
    monkeypatch.setattr(Docker, "unreadable", lambda self, path: True)
    args, error = client.compose_argv("services", "up")
    assert args == []
    assert error.startswith("EACCES:")
    assert "/home/pi/services/docker-compose.yml" in error


def test_unreadable_returns_false_for_missing_paths(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    assert client.unreadable(str(tmp_path / "nope.yml")) is False
    assert client.unreadable(str(tmp_path)) is False


# ── tasks ────────────────────────────────────────────────────────────────────


def test_task_reports_progress_then_completion(tmp_path: Path):
    settings = make_settings(tmp_path)
    stub = Stub()
    client = Docker(settings, stub)
    started = client.start_task(client.argv("pull", "alpine:3"), "pull alpine:3")
    assert started["ok"] is True
    assert "docker pull alpine:3" in stub.background[0]

    status = client.task_status(started["id"])
    assert status["done"] is True
    assert status["success"] is True
    assert status["returncode"] == 0


def test_task_status_rejects_path_traversal(tmp_path: Path):
    client = Docker(make_settings(tmp_path), Stub())
    assert client.task_status("../../etc/passwd")["ok"] is False


# ── validation ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", ["", "bad name", "../etc", "a;rm -rf /", "x" * 70])
def test_invalid_names_are_rejected(value: str):
    assert docker_api.valid_container_ref(value) is False
    assert docker_api.valid_name(value) is False


@pytest.mark.parametrize(
    "value",
    ["alpine", "alpine:3.20", "ghcr.io/foo/bar:latest", "registry:5000/repo/image:1.0"],
)
def test_valid_image_references(value: str):
    assert valid_image_ref(value) is True


@pytest.mark.parametrize("value", ["Alpine", "alpine:3.20 ", "repo image", "../image"])
def test_invalid_image_references(value: str):
    assert valid_image_ref(value) is False


@pytest.mark.parametrize("value", ["2h", "30m", "10s", "2026-11-20", "2026-11-20T10:00:00"])
def test_since_values(value: str):
    assert valid_since(value) is True


@pytest.mark.parametrize("value", ["2 hours", "; rm -rf /", "$(id)"])
def test_invalid_since_values(value: str):
    assert valid_since(value) is False


# ── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.routers import docker as docker_router

    settings = make_settings(tmp_path)
    stub = Stub()
    monkeypatch.setattr(docker_router, "get_executor", lambda _settings: stub)
    with TestClient(create_app(settings)) as test_client:
        test_client.stub = stub
        yield test_client


def csrf(client: TestClient) -> str:
    body = client.get("/docker").text
    return re.search(r'name="csrf-token" content="([^"]+)"', body).group(1)


def test_page_renders_all_tabs(client: TestClient):
    response = client.get("/docker")
    assert response.status_code == 200
    body = response.text
    for tab in ("Containers", "Compose", "Images", "Volumes", "Networks", "System"):
        assert tab in body
    # The page must not look busy before the first fetch finishes.
    assert "dockerUnavailable" in body


def test_container_more_menu_escapes_the_table_scroll_box(client: TestClient):
    """The three-dots dropdown must not be clipped by .table-responsive.

    The containers table scrolls horizontally. An absolutely positioned
    dropdown inside it gets cut off and stretches the table into showing a
    stray vertical scrollbar, so the menu is anchored to the viewport instead.
    """
    html = client.get("/docker").text
    assert "bootstrap.Dropdown.getOrCreateInstance(toggle" in html
    assert "popperConfig: { strategy: 'fixed' }" in html


def test_page_is_hidden_when_the_module_is_off(tmp_path: Path):
    settings = make_settings(tmp_path, docker_enabled=False)
    with TestClient(create_app(settings)) as off_client:
        assert off_client.get("/docker").status_code == 404
        assert off_client.get("/ajax/docker/ps").status_code == 404


def test_ps_endpoint_returns_containers(client: TestClient):
    data = client.get("/ajax/docker/ps").json()
    assert data["available"] is True
    assert [row["name"] for row in data["containers"]] == ["cloudreve", "xunlei"]


def test_network_inspect_endpoint_returns_details(client: TestClient):
    data = client.get("/ajax/docker/networks/services_default/inspect").json()
    assert data["ok"] is True
    assert data["network"]["name"] == "services_default"
    assert data["network"]["subnets"][0]["gateway"] == "172.20.0.1"
    assert [c["name"] for c in data["network"]["containers"]] == ["cloudreve"]


def test_network_inspect_endpoint_rejects_bad_names(client: TestClient):
    response = client.get("/ajax/docker/networks/bad%20name/inspect")
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_page_wires_network_details_button(client: TestClient):
    html = client.get("/docker").text
    assert "openNetworkDetails(network.name)" in html
    assert "/ajax/docker/networks/' + encodeURIComponent(name) + '/inspect" in html
    assert 'id="dockerDetailsIcon"' in html


def test_ps_endpoint_degrades_when_the_daemon_is_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.routers import docker as docker_router

    settings = make_settings(tmp_path)
    monkeypatch.setattr(docker_router, "get_executor", lambda _settings: Stub(reason="daemon"))
    with TestClient(create_app(settings)) as degraded:
        data = degraded.get("/ajax/docker/ps").json()
    assert data["available"] is False
    assert data["reason"] == "daemon"
    assert data["containers"] == []


def test_action_builds_a_safe_argv(client: TestClient):
    response = client.post(
        "/ajax/docker/action",
        json={"id": "cloudreve", "act": "stop"},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert client.stub.commands[-1] == ["docker", "stop", "cloudreve"]


def test_action_rejects_injection_attempts(client: TestClient):
    response = client.post(
        "/ajax/docker/action",
        json={"id": "cloudreve; rm -rf /", "act": "stop"},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 400
    assert not any("rm -rf" in " ".join(argv) for argv in client.stub.commands)


def test_action_requires_csrf(client: TestClient):
    response = client.post("/ajax/docker/action", json={"id": "cloudreve", "act": "stop"})
    assert response.status_code == 403


def test_kill_requires_a_known_signal(client: TestClient):
    token = csrf(client)
    assert client.post(
        "/ajax/docker/action",
        json={"id": "cloudreve", "act": "kill", "signal": "SIGTERM"},
        headers={"X-CSRF-Token": token},
    ).json()["ok"] is True
    assert client.post(
        "/ajax/docker/action",
        json={"id": "cloudreve", "act": "kill", "signal": "SIG; reboot"},
        headers={"X-CSRF-Token": token},
    ).status_code == 400


def test_logs_endpoint_clamps_lines(client: TestClient):
    response = client.get("/ajax/docker/containers/cloudreve/logs", params={"lines": 99999})
    assert response.status_code == 200
    assert "2000" in client.stub.commands[-1]


def test_compose_action_starts_a_background_task(client: TestClient):
    response = client.post(
        "/ajax/docker/compose/action",
        json={"project": "services", "act": "restart"},
        headers={"X-CSRF-Token": csrf(client)},
    )
    payload = response.json()
    assert payload["ok"] is True
    assert payload["id"]
    assert "docker compose" in client.stub.background[-1]
    assert "restart" in client.stub.background[-1]


def test_compose_action_reports_permission_denied(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
):
    from app.docker import Docker

    monkeypatch.setattr(Docker, "unreadable", lambda self, path: True)
    response = client.post(
        "/ajax/docker/compose/action",
        json={"project": "services", "act": "restart"},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 403
    payload = response.json()
    assert payload["ok"] is False
    assert payload["reason"] == "denied"
    assert payload["path"] == "/home/pi/services/docker-compose.yml"


def test_compose_action_is_rejected_when_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from app.routers import docker as docker_router

    settings = make_settings(tmp_path, compose_enabled=False)
    monkeypatch.setattr(docker_router, "get_executor", lambda _settings: Stub())
    with TestClient(create_app(settings)) as disabled:
        token = csrf(disabled)
        response = disabled.post(
            "/ajax/docker/compose/action",
            json={"project": "services", "act": "up"},
            headers={"X-CSRF-Token": token},
        )
    assert response.status_code == 403


def test_prune_preview_returns_sizes_without_running_anything(client: TestClient):
    response = client.post(
        "/ajax/docker/system/prune",
        json={"targets": ["images", "buildcache"], "preview": True},
        headers={"X-CSRF-Token": csrf(client)},
    )
    data = response.json()
    assert data["preview"] is True
    assert {row["type"] for row in data["rows"]} == {"Images", "Build Cache"}
    assert client.stub.background == []


def test_task_endpoint_serves_a_finished_task(client: TestClient, tmp_path: Path):
    settings = client.app.state.settings
    task_dir = settings.data_dir / "docker_tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    task_id = time.strftime("%Y%m%d%H%M%S") + "-abcdef"
    (task_dir / f"{task_id}.log").write_text(
        "Step 1\nStep 2\n__PIWEB_TASK_DONE__ rc=1\n", encoding="utf-8"
    )
    data = client.get(f"/ajax/docker/task/{task_id}").json()
    assert data["done"] is True
    assert data["success"] is False
    assert data["returncode"] == 1
    assert "Step 2" in data["output"]
