"""Shutdown from the actions page.

The power-off is detached so the response can reach the browser first. It used
to throw the command output away with >/dev/null and report success no matter
what happened, so a failed sudo (or any other error) was invisible: the page
said "shutdown scheduled" while the Pi was still up.

These tests pin the replacement: the launch goes through run_background into a
log the operator can read, the return value is checked, and failures come back
as an error on the page.
"""

from types import SimpleNamespace

from app.executor import CommandResult
from app.routers import actions


class StubExecutor:
    def __init__(self, result=None):
        self.result = result if result is not None else CommandResult(stdout="started")
        self.calls: list[tuple[str, object]] = []

    def run_background(self, command, log_path):
        self.calls.append((command, log_path))
        return self.result


class FakeRequest:
    def __init__(self, settings):
        self.app = SimpleNamespace(state=SimpleNamespace(settings=settings))
        self.session: dict[str, object] = {}
        self.headers: dict[str, str] = {}
        self.client = SimpleNamespace(host="127.0.0.1")


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


def _shutdown(tmp_path, monkeypatch, executor):
    monkeypatch.setattr(actions, "get_executor", lambda settings: executor)
    request = FakeRequest(_settings(tmp_path))
    actions.actions_run(request, action="shutdown")
    return request.session["action_result"]


def test_shutdown_logs_poweroff_instead_of_dev_null(tmp_path, monkeypatch):
    executor = StubExecutor()
    result = _shutdown(tmp_path, monkeypatch, executor)

    assert len(executor.calls) == 1
    command, log_path = executor.calls[0]
    assert "sudo poweroff" in command
    assert "/dev/null" not in command
    assert log_path.name == "poweroff.log"
    assert "poweroff.log" in result["output"]


def test_shutdown_reports_launch_failure(tmp_path, monkeypatch):
    executor = StubExecutor(CommandResult(returncode=1, stderr="sudo: not allowed"))
    result = _shutdown(tmp_path, monkeypatch, executor)

    assert result["level"] == "danger"
    assert "sudo: not allowed" in result["output"]
    assert "scheduled" not in result["message"].lower()


def test_successful_launch_is_still_flagged_as_scheduled(tmp_path, monkeypatch):
    result = _shutdown(tmp_path, monkeypatch, StubExecutor())

    assert result["level"] == "warning"
    assert "scheduled" in result["message"].lower()
