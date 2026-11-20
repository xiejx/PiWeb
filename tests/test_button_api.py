"""/api.php is gone; the button API keeps working through /api/button.

The PHP-era alias was removed on purpose, so the first test pins it to 404 and
the second keeps the documented endpoints from silently disappearing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

KEY = "a" * 32


@pytest.fixture()
def client(tmp_path: Path):
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
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_php_alias_is_gone(client: TestClient):
    assert client.get("/api.php", params={"hash": KEY}).status_code == 404
    assert client.post("/api.php", data={"hash": KEY}).status_code == 404


def test_button_api_answers_and_stays_disabled_by_default(client: TestClient):
    response = client.get(f"/api/button/{KEY}")
    assert response.status_code == 403
    body = response.json()
    assert body["success"] is False
    assert "disabled" in body["error"].lower()


def test_button_api_accepts_the_key_from_a_header(client: TestClient):
    response = client.get("/api/button", headers={"X-PiWeb-Key": KEY})
    assert response.status_code == 403
    assert response.json()["success"] is False
