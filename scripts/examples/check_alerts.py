#!/usr/bin/env python3
"""Run PiWeb's alert threshold checks once. Intended to be called from cron.

The dashboard normally runs these checks whenever someone loads its page. If
nobody has the dashboard open, nothing is checked. Scheduling this script
(e.g. every five minutes) makes temperature / disk / throttling / Docker alerts
work even with no browser connected.

Login-lockout alerts are sent directly from the login flow, so they do not
depend on this script.
"""

from __future__ import annotations

import sys
from pathlib import Path

# /opt/piweb/scripts/check_alerts.py -> /opt/piweb
APP_ROOT = Path(__file__).resolve().parent.parent
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from app.alerts import evaluate, evaluate_docker  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.dashboard import collect  # noqa: E402
from app.executor import get_executor  # noqa: E402


def main() -> int:
    settings = load_settings()
    executor = get_executor(settings)
    data = collect(settings, executor)
    evaluate(settings, data)
    evaluate_docker(settings, executor)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
