"""The runtime info page is the only way into /modules, so keep it reachable.

The Modules card at the bottom of /system-info carries the "Manage modules"
button. That page had no navigation entry and its labels were missing from the
translation files, which made the module list effectively undiscoverable.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path, lang: str) -> Settings:
    settings = Settings(
        allow_unauthenticated=True,
        default_lang=lang,
        session_secret="test-secret",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "command_logs",
        scripts_dir=tmp_path / "user_scripts",
        backups_dir=tmp_path / "backups",
    )
    settings.ensure_dirs()
    return settings


@pytest.fixture()
def client(tmp_path: Path):
    with TestClient(create_app(make_settings(tmp_path, "zh"))) as test_client:
        yield test_client


def test_system_info_page_is_linked_from_the_navbar(client: TestClient):
    body = client.get("/").text
    assert 'href="/system-info"' in body
    assert "系统信息" in body


def test_modules_card_offers_a_translated_entry(client: TestClient):
    response = client.get("/system-info")
    assert response.status_code == 200
    body = response.text
    assert 'href="/modules"' in body
    assert "管理模块" in body
    assert "Manage modules" not in body


def test_legacy_phpinfo_path_is_gone(client: TestClient):
    """The 1.x redirect was removed on purpose; keep it from creeping back."""

    assert client.get("/phpinfo", follow_redirects=False).status_code == 404
