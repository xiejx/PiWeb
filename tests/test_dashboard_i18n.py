"""The dashboard must not fall back to English when the UI language is Chinese.

Most of its labels used to render from the English defaults because the keys
were missing from the translation files (or were spelled differently there),
and a few strings — the auth banner, the temperature hint, the "N of total
used" notes — were not routed through ``t()`` at all.
"""

from __future__ import annotations

import re
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
def body(tmp_path: Path):
    with TestClient(create_app(make_settings(tmp_path, "zh"))) as client:
        html = client.get("/").text
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))


def test_chinese_labels_are_rendered(body: str):
    for label in (
        "自定义菜单",
        "系统检查",
        "服务状态",
        "内存详情",
        "磁盘信息",
        "活跃用户",
        "主机名",
        "内核",
        "运行时间",
        "平均负载",
        "挂载点",
        "容量",
        "已用",
        "可用",
        "发送",
    ):
        assert label in body, f"missing Chinese label: {label}"


def test_english_defaults_are_gone(body: str):
    for leftover in (
        "Customize menu",
        "Memory details",
        "System information",
        "Logged-in users",
        "No interactive sessions",
        "Power / thermal warning",
        "No storage information",
        "% of",
        "used",
    ):
        assert leftover not in body, f"untranslated string: {leftover}"


def test_usage_notes_use_the_translated_template(body: str):
    assert "已用" in body and "，共" in body
    assert "（5 / 15 分钟）" in body


def test_auth_banner_is_translated(body: str):
    assert "认证已关闭" in body
    assert "Authentication is disabled" not in body
