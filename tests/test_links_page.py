"""Regression tests for the Web Navigation module.

Covers page rendering, link CRUD through the AJAX endpoints, the http(s)-only
URL boundary, and backup inclusion of data/links.json.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, _parse_modules
from app.main import create_app
from app.routers import backup as backup_router


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        allow_unauthenticated=True,
        default_lang="en",
        modules=_parse_modules({}),
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


def csrf(client: TestClient) -> str:
    body = client.get("/links").text
    return re.search(r'name="csrf-token" content="([^"]+)"', body).group(1)


def submit(client: TestClient, **fields):
    return client.post(
        "/ajax/links/submit",
        json=fields,
        headers={"X-CSRF-Token": csrf(client)},
    )


def test_page_renders_and_module_is_active(client: TestClient):
    response = client.get("/links")
    assert response.status_code == 200
    body = response.text
    assert "Web Navigation" in body
    assert 'href="/links"' in body
    assert "No links yet" in body


def test_submit_rejects_missing_csrf(client: TestClient):
    response = client.post("/ajax/links/submit", json={"title": "x", "url": "https://x.com"})
    assert response.status_code == 403


def test_add_link_and_render_grouped(client: TestClient):
    response = submit(
        client,
        title="GitHub",
        url="https://github.com",
        group="Dev",
        icon="GH",
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True

    page = client.get("/links").text
    assert "GitHub" in page
    assert "Dev" in page
    assert "github.com" in page
    assert 'rel="noopener noreferrer"' in page
    assert 'target="_blank"' in page


def test_http_scheme_is_added_when_missing(client: TestClient):
    response = submit(client, title="Example", url="example.com")
    assert response.status_code == 200
    link = response.json()["link"]
    assert link["url"] == "https://example.com"


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "vbscript:msgbox(1)",
        "",
    ],
)
def test_rejects_non_http_urls(client: TestClient, bad_url: str):
    response = submit(client, title="Bad", url=bad_url)
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_edit_link_preserves_position(client: TestClient):
    submit(client, title="A", url="https://a.com")
    submit(client, title="B", url="https://b.com")
    response = submit(
        client,
        link_id="0",
        title="A2",
        url="https://a2.com",
        group="",
        icon="",
    )
    assert response.status_code == 200
    page = client.get("/links").text
    assert "A2" in page
    assert "a2.com" in page
    assert "https://a.com" not in page


def test_delete_link(client: TestClient):
    submit(client, title="Tmp", url="https://tmp.com")
    response = client.post(
        "/ajax/links/delete",
        json={"id": "0"},
        headers={"X-CSRF-Token": csrf(client)},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "Tmp" not in client.get("/links").text


def test_links_file_in_backup_allowlist(client: TestClient):
    submit(client, title="Saved", url="https://saved.com")
    settings = client.app.state.settings
    bundle = backup_router.collect(settings)
    assert "data/links.json" in bundle
    stored = json.loads(base64.b64decode(bundle["data/links.json"]))
    assert stored[0]["title"] == "Saved"
