from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from ..executor import get_executor
from ..security import auth_log, client_ip
from ..templating import render

router = APIRouter()


def parse_readall(text: str) -> tuple[list[dict], int]:
    lines = text.splitlines()
    header: list[str] | None = None
    physical_col = -1
    rows: list[dict] = []

    for line in lines:
        if "|" not in line:
            continue
        raw = line.replace("||", "\x00")
        cells = [cell.strip().replace("\x00", " ") for cell in raw.split("|")][1:-1]
        if not cells:
            continue
        if header is None and any("BCM" in cell.upper() for cell in cells):
            header = cells
            for index, cell in enumerate(cells):
                if "PHYSICAL" in cell.upper():
                    physical_col = index
                    break
            continue
        if header is None or len(cells) != len(header):
            continue
        if all(re.fullmatch(r"[+\-=]+", cell) for cell in cells):
            continue
        if not any(cell for cell in cells):
            continue

        def side(start: int, end: int) -> dict[str, str]:
            values: dict[str, str] = {}
            for index in range(start, end):
                key = header[index].lower()
                if key == "bcm" or key == "wpi" or key == "name" or key == "mode" or key == "v" or key == "pud":
                    values[key] = cells[index]
            return values

        left = side(0, physical_col) if physical_col > 0 else {}
        right = side(physical_col + 1, len(cells)) if physical_col >= 0 else {}
        physical = cells[physical_col] if physical_col >= 0 else ""
        rows.append(
            {
                "left": left,
                "right": right,
                "physical": physical.replace(" ", " | "),
                "left_gpio": left.get("bcm", "").isdigit(),
                "right_gpio": right.get("bcm", "").isdigit(),
            }
        )
    return rows, physical_col


def gpio_state(executor) -> dict:
    wiring = executor.run("gpio readall 2>/dev/null", timeout=15)
    if wiring.stdout.strip():
        rows, physical_col = parse_readall(wiring.stdout)
        return {"source": "wiringpi", "rows": rows, "raw": wiring.stdout, "physical_col": physical_col}
    raspi = executor.run("raspi-gpio get 2>/dev/null", timeout=15)
    if raspi.stdout.strip():
        return {"source": "raspi-gpio", "rows": [], "raw": raspi.stdout, "physical_col": -1}
    return {"source": "none", "rows": [], "raw": "", "physical_col": -1}


@router.get("/gpio", response_class=HTMLResponse)
def gpio_page(request: Request):
    settings = request.app.state.settings
    return render(request, "gpio.html", gpio=gpio_state(get_executor(settings)))


@router.post("/gpio/set")
async def gpio_set(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    try:
        payload = await request.json()
        bcm = int(payload.get("bcm"))
        field = str(payload.get("field", ""))
        value = str(payload.get("value", ""))
    except (TypeError, ValueError, AttributeError):
        return JSONResponse({"ok": False, "error": "Invalid request"}, status_code=400)

    if field == "mode" and value in {"in", "out", "pwm", "clock", "up", "down", "tri"}:
        command = f"gpio -g mode {bcm} {value}"
    elif field == "v" and value in {"0", "1"}:
        command = f"gpio -g write {bcm} {value}"
    elif field == "pud" and value in {"up", "down"}:
        command = f"gpio -g mode {bcm} {value}"
    else:
        return JSONResponse({"ok": False, "error": "Invalid GPIO field or value"}, status_code=400)

    result = executor.run(command, timeout=15)
    auth_log(settings, "gpio", command, client_ip(request, settings),
             request.headers.get("user-agent", ""))
    if not result.ok:
        return JSONResponse({"ok": False, "error": result.output or "gpio command failed"}, status_code=500)
    return JSONResponse({"ok": True, "command": command, "output": result.output})
