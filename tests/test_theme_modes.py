"""Theme handling: light, dark, and "follow system".

The panel used to offer only a light/dark toggle that wrote its choice to
localStorage; a first visit silently inherited the OS setting but there was no
way back to it. These tests pin the three-way picker and the resolution the
inline head script performs before the first paint.
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
APP_JS = ROOT / "app" / "static" / "js" / "app.js"
I18N = ROOT / "app" / "i18n"


@pytest.fixture()
def client(tmp_path):
    settings = Settings(
        allow_unauthenticated=True,
        default_lang="zh",
        session_secret="test-secret",
        data_dir=tmp_path / "data",
        buttons_dir=tmp_path / "buttons",
        command_logs_dir=tmp_path / "logs",
        scripts_dir=tmp_path / "scripts",
        backups_dir=tmp_path / "backups",
    )
    settings.ensure_dirs()
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def _inline_theme_script(html: str) -> str:
    match = re.search(r"localStorage\.getItem\('piweb-theme'\).*?\} catch", html, re.S)
    assert match, "theme bootstrap script is missing"
    return match.group(0)


def test_pages_resolve_system_as_a_mode(client: TestClient):
    for path in ("/", "/login", "/setup"):
        html = client.get(path).text
        script = _inline_theme_script(html)
        # Stored value must be validated, and "system" must be a real option.
        assert "saved === 'system'" in script
        assert "data-theme-mode" in script
        # A saved "system" used to be read as "not dark".
        assert "mode === 'dark'" in script


def test_navbar_offers_three_choices(client: TestClient):
    html = client.get("/").text
    assert html.count('data-theme-set="') == 3
    for label in ("浅色", "深色", "跟随系统"):
        assert label in html
    assert 'data-theme-icon' in html


def test_login_page_offers_the_same_choices(client: TestClient):
    html = client.get("/login").text
    assert html.count('data-theme-set="') == 3
    # The control sits in the corner; a dropup used to cover the submit button.
    assert "position-fixed top-0 end-0" in html


def test_app_js_follows_the_os_while_system_is_selected():
    source = APP_JS.read_text(encoding="utf-8")
    assert '"light", "dark", "system"' in source
    assert "prefers-color-scheme: dark" in source
    assert 'addEventListener("change", onSchemeChange)' in source
    # The old toggle only knew two states.
    assert 'getElementById("themeIcon")' not in source


def test_translations_cover_the_picker():
    en = json.loads((I18N / "en.json").read_text(encoding="utf-8"))
    zh = json.loads((I18N / "zh.json").read_text(encoding="utf-8"))
    for key in ("nav.theme", "theme.light", "theme.dark", "theme.system"):
        assert key in en and key in zh, key
    assert zh["theme.system"] == "跟随系统"
    for lang in ("de", "es", "fr", "uk"):
        data = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
        assert "nav.theme" in data, lang
    # The old login-only toggle label is gone with the button.
    assert "login.toggle_theme" not in en
    assert "login.toggle_theme" not in zh
