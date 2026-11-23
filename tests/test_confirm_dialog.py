"""The shared confirmation dialog replaced window.confirm().

The dialog markup lives in base.html and is driven by PiWeb.confirm() in
app.js. Templates should not call the native prompt any more: it ignores the
panel theme and looks foreign on a phone.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import ModuleConfig, Settings
from app.main import create_app

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
APP_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "app.js"


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
    settings.modules = {
        key: ModuleConfig(key=key, title=key.title(), enabled=True)
        for key in ("actions", "docker", "scripts", "rpi")
    }
    settings.ensure_dirs()
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def test_layout_ships_the_dialog(client: TestClient):
    html = client.get("/actions").text
    assert 'id="piwebConfirmModal"' in html
    assert 'id="piwebConfirmOk"' in html
    assert 'id="piwebConfirmCancel"' in html
    # Translated, so a Chinese session does not get an English button.
    assert "请确认" in html
    assert "确认" in html


def test_confirm_question_is_attached_to_forms(client: TestClient):
    html = client.get("/actions").text
    assert 'data-confirm="确定执行该命令？"' in html
    assert 'data-confirm-again="' in html  # shutdown asks twice


def test_app_js_exposes_the_dialog():
    source = APP_JS.read_text(encoding="utf-8")
    assert "confirm: confirmDialog" in source
    assert "new bootstrap.Modal(el)" in source
    # The native prompt survives only as the fallback for pages without the
    # layout (login / setup); the comment above the helper mentions it once.
    assert source.count("return Promise.resolve(window.confirm(message))") == 1


def test_double_confirm_settles_after_the_fade_out():
    """Regression: OK must not resolve while the modal is still transitioning.

    The shutdown form asks twice. The old handler resolved on the OK click and
    hid the modal afterwards; the second dialog's show() then landed while the
    first was still fading out and was ignored, and the first dialog's pending
    hidden event settled the second promise as false. The form never posted,
    so the Pi stayed on. Resolve on hidden.bs.modal instead, and let the OK
    handler only record the answer.
    """
    source = APP_JS.read_text(encoding="utf-8")
    ok_handler = re.search(
        r'piwebConfirmOk"\)\.addEventListener\("click", function \(\) \{(.*?)\}\);',
        source,
        re.S,
    )
    assert ok_handler, "OK click handler not found"
    # No settle/resolve on the click itself — only record the answer, then hide.
    assert "settleConfirm" not in ok_handler.group(1)
    assert "answer = true" in ok_handler.group(1)
    assert "confirmModal.hide()" in ok_handler.group(1)

    hidden_handler = re.search(
        r'addEventListener\("hidden\.bs\.modal", function \(\) \{(.*?)\}\);',
        source,
        re.S,
    )
    assert hidden_handler, "hidden.bs.modal handler not found"
    # The answer is settled once the fade-out has completed, using the answer
    # recorded by the OK handler (default false for Escape / Cancel / backdrop).
    assert "settleConfirm(currentConfirm.answer)" in hidden_handler.group(1)


def test_no_template_calls_the_native_prompt():
    """A bare confirm(...) would bypass the modal and its translations."""
    pattern = re.compile(r"(?<![.\w])confirm\s*\(")
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "data-confirm" in line or "confirm_" in line:
                continue
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()[:80]}")
    assert offenders == []


def test_layout_ships_the_prompt_dialog(client: TestClient):
    html = client.get("/actions").text
    for marker in (
        'id="piwebPromptModal"',
        'id="piwebPromptInput"',
        'id="piwebPromptOk"',
        'id="piwebPromptCancel"',
    ):
        assert marker in html


def test_app_js_exposes_the_prompt_dialog():
    source = APP_JS.read_text(encoding="utf-8")
    assert "prompt: promptDialog" in source
    assert "options.password" in source
    assert "settlePrompt(null)" in source


def test_no_template_calls_the_native_window_prompt():
    """window.prompt blocks the browser and ignores the panel theme."""
    offenders = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "window.prompt(" in line:
                offenders.append(f"{path.name}:{number}: {line.strip()[:80]}")
    assert offenders == []
