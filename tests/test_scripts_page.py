"""Regression tests for the Scripts page.

`scripts_with_schedules` used to build its marker list before the loop that
binds `name`, so every call raised UnboundLocalError and both `/scripts` and
`/scripts/list` answered 500. These tests pin the function's behaviour and the
page/list responses so the regression cannot come back unnoticed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.cron_store import scripts_with_schedules
from app.executor import CommandResult
from app.main import create_app
from app.routers import scripts as scripts_router

SCRIPT_PATH = "/home/pi/PiWeb/user_scripts/backup.sh"


class StubExecutor:
    """Stand-in for Executor that returns a canned crontab instead of shelling out."""

    def __init__(self, crontab: str = "") -> None:
        self.crontab = crontab
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
        return CommandResult(stdout=self.crontab, returncode=0)


# ── scripts_with_schedules ───────────────────────────────────────────────────


def test_no_scripts_returns_empty_mapping():
    # The original crash: the marker tuple referenced `name` before the loop
    # bound it, so even an empty script list blew up.
    assert scripts_with_schedules(StubExecutor(""), []) == {}


def test_empty_crontab_yields_no_schedules():
    assert scripts_with_schedules(StubExecutor(""), ["backup.sh"]) == {}


def test_finds_schedule_with_current_marker():
    crontab = f"0 4 * * * bash {SCRIPT_PATH} # piweb-script:backup.sh\n"
    assert scripts_with_schedules(StubExecutor(crontab), ["backup.sh"]) == {
        "backup.sh": f"0 4 * * * bash {SCRIPT_PATH}",
    }


def test_ignores_the_old_pre_rename_marker():
    crontab = f"*/5 * * * * bash {SCRIPT_PATH} # gumcp-script:backup.sh\n"
    assert scripts_with_schedules(StubExecutor(crontab), ["backup.sh"]) == {}


def test_ignores_unrelated_jobs_and_other_scripts():
    crontab = "\n".join(
        [
            "MAILTO=pi",
            "0 1 * * * bash /home/pi/other.sh # piweb-script:other.sh",
            "",
        ]
    )
    assert scripts_with_schedules(StubExecutor(crontab), ["backup.sh"]) == {}


def test_matches_each_script_exactly_even_with_shared_suffix():
    crontab = "\n".join(
        [
            f"0 4 * * * bash {SCRIPT_PATH} # piweb-script:backup.sh",
            "30 2 * * * bash /home/pi/PiWeb/user_scripts/db-backup.sh "
            "# piweb-script:db-backup.sh",
            "0 * * * * python3 /home/pi/PiWeb/user_scripts/poll.py "
            "# piweb-script:poll.py",
        ]
    )
    found = scripts_with_schedules(
        StubExecutor(crontab), ["backup.sh", "db-backup.sh", "poll.py", "missing.sh"]
    )
    assert found == {
        "backup.sh": f"0 4 * * * bash {SCRIPT_PATH}",
        "db-backup.sh": "30 2 * * * bash /home/pi/PiWeb/user_scripts/db-backup.sh",
        "poll.py": "0 * * * * python3 /home/pi/PiWeb/user_scripts/poll.py",
    }


def test_reads_the_crontab_once_for_many_scripts():
    executor = StubExecutor("")
    scripts_with_schedules(executor, ["a.sh", "b.sh", "c.py"])
    assert len(executor.commands) == 1


# ── HTTP surface ─────────────────────────────────────────────────────────────


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    settings = Settings(
        allow_unauthenticated=True,
        session_secret="test-secret",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "command_logs",
        scripts_dir=tmp_path / "user_scripts",
        backups_dir=tmp_path / "backups",
    )
    settings.ensure_dirs()
    (settings.scripts_dir / "backup.sh").write_text("#!/bin/bash\n", encoding="utf-8")

    crontab = f"0 4 * * * bash {SCRIPT_PATH} # piweb-script:backup.sh\n"
    monkeypatch.setattr(
        scripts_router, "get_executor", lambda _settings: StubExecutor(crontab)
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_scripts_page_renders(client: TestClient):
    response = client.get("/scripts")
    assert response.status_code == 200
    assert 'id="scriptEditor"' in response.text
    # The page hands the server-side schedule map to the editor as JSON.
    assert f"0 4 * * * bash {SCRIPT_PATH}" in response.text


def test_scripts_list_reports_schedules(client: TestClient):
    response = client.get("/scripts/list")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert [row["name"] for row in payload["scripts"]] == ["backup.sh"]
    assert payload["schedules"] == {"backup.sh": f"0 4 * * * bash {SCRIPT_PATH}"}
