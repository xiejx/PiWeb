"""Tests for the Raspberry Pi metrics history location.

The temperature/frequency history is a short-lived cache. It lives under
command_logs/ instead of data/ so sampling it every 30 seconds avoids wearing
the SD card on systems that mount that directory as tmpfs.
"""

from __future__ import annotations

import json

from app.config import Settings
from app.executor import CommandResult
from app.routers import rpi as rpi_router


class ClockStub:
    """Minimal executor for the vcgencmd calls metrics_history makes."""

    def run(self, command: str, timeout: float = 30.0, **kwargs) -> CommandResult:
        if "measure_temp" in command:
            return CommandResult(stdout="temp=45.0'C")
        if "measure_clock" in command:
            return CommandResult(stdout="frequency=1500000000")
        return CommandResult(stdout="", returncode=1)


def make_settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        command_logs_dir=tmp_path / "command_logs",
    )


def test_history_file_lives_under_command_logs(tmp_path):
    settings = make_settings(tmp_path)
    path = rpi_router._history_file(settings)
    assert path == settings.command_logs_dir / "rpi_metrics_history.json"


def test_metrics_history_writes_to_command_logs(tmp_path):
    settings = make_settings(tmp_path)
    history = rpi_router.metrics_history(settings, ClockStub())
    assert history
    assert (settings.command_logs_dir / "rpi_metrics_history.json").exists()
    assert not (settings.data_dir / "rpi_metrics_history.json").exists()
