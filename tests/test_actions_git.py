"""Updating PiWeb from the panel.

The update panel used to run `sudo git ...`. On a service without a terminal
sudo cannot answer the password prompt, so every click ended with
"sudo: a terminal is required to read the password", and on a copy-install
(the installer's rsync branch leaves no .git) even a passwordless sudo would
have hit "not a git repository".

These tests pin the replacement: git runs as the service user, the repo state
is checked first, and failures come back as a reason the UI can translate.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import BASE_DIR
from app.executor import CommandResult
from app.routers import actions


class StubExecutor:
    """Answers git commands from a list of (needle, result) pairs."""

    def __init__(self, responses):
        self.responses = responses
        self.commands: list[str] = []

    def run(self, command, timeout=30.0, **kwargs):
        self.commands.append(command)
        for needle, result in self.responses:
            if needle in command:
                return result
        return CommandResult(returncode=1, stderr="unexpected command")

    def ran(self, needle: str) -> bool:
        return any(needle in command for command in self.commands)


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(stdout=stdout, returncode=0)


def failed(stderr: str) -> CommandResult:
    return CommandResult(stderr=stderr, returncode=128)


NOT_A_REPO = failed("fatal: not a git repository (or any of the parent directories): .git")


def test_copy_install_is_reported_as_not_a_repo():
    executor = StubExecutor([("rev-parse", NOT_A_REPO)])
    state = actions.git_repo_state(executor)
    assert state == {"repo": False, "remote": ""}
    command, error = actions.git_update_command("master", state)
    assert command == ""
    assert "not a git checkout" in error
    # The check must not stop at "not a repo" and try to fetch anyway.
    assert not executor.ran("fetch")


def test_checkout_reports_its_origin():
    executor = StubExecutor([
        ("rev-parse", ok("true\n")),
        ("remote get-url origin", ok("https://github.com/xiejx/PiWeb.git\n")),
    ])
    assert actions.git_repo_state(executor) == {
        "repo": True,
        "remote": "https://github.com/xiejx/PiWeb.git",
    }


def test_checkout_without_origin_cannot_pull():
    executor = StubExecutor([
        ("rev-parse", ok("true\n")),
        ("remote get-url origin", failed("error: No such remote 'origin'")),
    ])
    state = actions.git_repo_state(executor)
    command, error = actions.git_update_command("master", state)
    assert command == ""
    assert "origin" in error


def test_update_commands_never_call_sudo():
    state = {"repo": True, "remote": "git@github.com:xiejx/PiWeb.git"}
    for command, error in (
        actions.git_update_command("master", state),
        actions.git_update_command("v2.2.2", state),
    ):
        assert error == ""
        assert "sudo" not in command
        assert command.startswith("git -C ")
        assert str(BASE_DIR) in command


def test_update_resets_to_a_tag_and_pulls_fast_forward():
    state = {"repo": True, "remote": "origin"}
    master, _ = actions.git_update_command("master", state)
    assert "pull --ff-only origin master" in master
    tag, _ = actions.git_update_command("v2.2.2", state)
    # --force: a deployment checkout mirrors the remote, so a moved tag
    # must not turn the refresh into "would clobber existing tag".
    assert "fetch --tags --force origin" in tag
    assert "reset --hard refs/tags/v2.2.2" in tag
    bad, error = actions.git_update_command("../../etc", state)
    assert bad == "" and error == "Invalid release tag."


@pytest.mark.parametrize(
    "output,reason",
    [
        ("sudo: a terminal is required to read the password", "failed"),
        ("bash: git: command not found", "git_missing"),
        ("fatal: not a git repository", "not_a_repo"),
        ("fatal: 'origin' does not appear to be a git repository", "no_remote"),
        ("error: unable to create file .git/FETCH_HEAD: Permission denied", "permission"),
        ("fatal: unable to access 'https://github.com/': Could not resolve host: github.com", "network"),
        ("fatal: Authentication failed for 'https://github.com/xiejx/PiWeb.git/'", "auth"),
        ("fatal: something else went wrong", "failed"),
    ],
)
def test_git_errors_map_to_translatable_reasons(output, reason):
    assert actions.classify_git_error(output) == reason


class FakeRequest:
    def __init__(self, settings):
        self.app = SimpleNamespace(state=SimpleNamespace(settings=settings))


def _settings(tmp_path):
    from app.config import Settings

    settings = Settings(
        allow_unauthenticated=True,
        session_secret="test",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "logs",
        scripts_dir=tmp_path / "scripts",
        backups_dir=tmp_path / "backups",
    )
    settings.ensure_dirs()
    return settings


def test_refresh_reports_copy_install_without_running_git_fetch(tmp_path, monkeypatch):
    executor = StubExecutor([("rev-parse", NOT_A_REPO)])
    monkeypatch.setattr(actions, "get_executor", lambda settings: executor)

    payload = actions.refresh_git_tags(FakeRequest(_settings(tmp_path))).body
    data = __import__("json").loads(payload)

    assert data["ok"] is False
    assert data["reason"] == "not_a_repo"
    assert data["tags"] == []
    assert not executor.ran("fetch")
    assert not any("sudo" in command for command in executor.commands)


def test_refresh_returns_new_tags_after_a_successful_fetch(tmp_path, monkeypatch):
    executor = StubExecutor([
        ("rev-parse", ok("true\n")),
        ("remote get-url origin", ok("https://github.com/xiejx/PiWeb.git\n")),
        ("fetch --tags", ok("")),
        ("tag --sort", ok("v2.2.3\nv2.2.2\n")),
    ])
    monkeypatch.setattr(actions, "get_executor", lambda settings: executor)

    data = __import__("json").loads(
        actions.refresh_git_tags(FakeRequest(_settings(tmp_path))).body
    )
    assert data["ok"] is True
    assert data["tags"] == ["v2.2.3", "v2.2.2"]
    assert not any("sudo" in command for command in executor.commands)


def test_refresh_explains_a_network_failure(tmp_path, monkeypatch):
    executor = StubExecutor([
        ("rev-parse", ok("true\n")),
        ("remote get-url origin", ok("https://github.com/xiejx/PiWeb.git\n")),
        ("fetch --tags", failed("fatal: unable to access ...: Could not resolve host: github.com")),
        ("tag --sort", ok("v2.2.2\n")),
    ])
    monkeypatch.setattr(actions, "get_executor", lambda settings: executor)

    data = __import__("json").loads(
        actions.refresh_git_tags(FakeRequest(_settings(tmp_path))).body
    )
    assert data["ok"] is False
    assert data["reason"] == "network"
    assert data["error"]  # raw tail kept for the details line
    assert data["tags"] == ["v2.2.2"]
