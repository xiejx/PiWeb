"""Password change feature: web endpoint, shared policy, and installer wiring."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.security import hash_password, password_policy_errors, verify_password

ROOT = Path(__file__).resolve().parents[1]


def make_settings(tmp_path: Path, **overrides) -> Settings:
    defaults = dict(
        # Keep the endpoint reachable without simulating a login; the route
        # itself does not depend on login_required.
        login_required=False,
        allow_unauthenticated=True,
        login_user="admin",
        login_pass=hash_password("old-password"),
        session_secret="test-secret",
        config_path=tmp_path / "config.toml",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "command_logs",
        scripts_dir=tmp_path / "user_scripts",
        backups_dir=tmp_path / "backups",
    )
    defaults.update(overrides)
    settings = Settings(**defaults)
    settings.ensure_dirs()
    return settings


def csrf_token(client: TestClient) -> str:
    body = client.get("/check").text
    return re.search(r'name="csrf-token" content="([^"]+)"', body).group(1)


def change(client, current: str, new: str, confirm: str):
    return client.post(
        "/ajax/check/password",
        headers={"X-CSRF-Token": csrf_token(client)},
        json={
            "current_password": current,
            "new_password": new,
            "confirm_password": confirm,
        },
    )


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# ── Shared policy ────────────────────────────────────────────────────────────

def test_policy_rejects_short_and_weak_passwords():
    assert password_policy_errors("short")  # < 8
    assert password_policy_errors("raspberry")  # known default
    assert password_policy_errors("PASSWORD")  # case-insensitive default
    assert password_policy_errors("") 


def test_policy_accepts_a_strong_password():
    assert password_policy_errors("a-Good-Long-pass9") == []


# ── Web endpoint ─────────────────────────────────────────────────────────────

def test_page_shows_password_card(client: TestClient):
    body = client.get("/check").text
    assert "登录密码" in body  # default lang is zh
    assert "当前密码" in body
    assert 'id="pw-current"' in body


def test_wrong_current_password_is_rejected(client: TestClient, settings: Settings):
    old_hash = settings.login_pass
    response = change(client, "wrong-old", "new-password-9", "new-password-9")
    assert response.status_code == 400
    assert "incorrect" in response.json()["error"].lower()
    assert settings.login_pass == old_hash
    assert not settings.config_path.exists() or "password" not in settings.config_path.read_text(encoding="utf-8")


def test_mismatched_new_passwords_are_rejected(client: TestClient, settings: Settings):
    response = change(client, "old-password", "new-password-9", "different-9")
    assert response.status_code == 400
    assert "match" in response.json()["error"].lower()


def test_weak_new_password_is_rejected(client: TestClient, settings: Settings):
    response = change(client, "old-password", "raspberry", "raspberry")
    assert response.status_code == 400
    assert "default" in response.json()["error"].lower()


def test_successful_change_writes_hash_and_updates_memory(client: TestClient, settings: Settings):
    response = change(client, "old-password", "brand-new-pass9", "brand-new-pass9")
    assert response.status_code == 200
    assert response.json()["ok"] is True

    # In-memory settings updated so the next login works without a restart.
    assert verify_password(settings.login_pass, "brand-new-pass9")
    assert not verify_password(settings.login_pass, "old-password")
    assert settings.login_pass.startswith("pbkdf2_sha256$")

    # Persisted to config.toml as a hash, not plaintext.
    text = settings.config_path.read_text(encoding="utf-8")
    assert "pbkdf2_sha256$" in text
    assert "brand-new-pass9" not in text
    assert 'user = "admin"' in text

    # Audit trail records the change.
    log = (settings.command_logs_dir / "auth.log").read_text(encoding="utf-8")
    assert "password_changed" in log


def test_csrf_required(client: TestClient):
    response = client.post(
        "/ajax/check/password",
        json={
            "current_password": "old-password",
            "new_password": "new-password-9",
            "confirm_password": "new-password-9",
        },
    )
    assert response.status_code == 403


def test_system_account_mode_disables_change(tmp_path: Path):
    settings = make_settings(tmp_path, login_check_system_user=True)
    with TestClient(create_app(settings)) as client:
        response = change(client, "old-password", "new-password-9", "new-password-9")
    assert response.status_code == 400
    assert "system account" in response.json()["error"].lower()


def test_page_hides_form_in_system_account_mode(tmp_path: Path):
    settings = make_settings(tmp_path, login_check_system_user=True)
    with TestClient(create_app(settings)) as client:
        body = client.get("/check").text
    assert 'id="pw-current"' not in body  # the form is hidden
    assert "passwd" in body


# ── Installer wiring ─────────────────────────────────────────────────────────

def test_installer_uses_interactive_password_prompt():
    text = (ROOT / "installer.sh").read_text(encoding="utf-8")
    assert "PIWEB_ADMIN_USER" in text  # username still defaults to admin
    assert "PIWEB_ADMIN_PASSWORD" not in text  # no env var for the password
    assert "read -r -s -p" in text  # interactive, hidden input
    assert "${#ADMIN_PASSWORD}" in text  # length check in prompt loop
    assert "[ -t 0 ]" in text  # only prompt on a real terminal
    assert "repeat password" in text.lower() or "Repeat password" in text
    assert "password_policy_errors" in text
    assert "auth_configured" in text
