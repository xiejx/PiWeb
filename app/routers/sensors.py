from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import sensors as sensor_lib
from ..executor import get_executor
from ..templating import render

router = APIRouter()


@router.get("/sensors", response_class=HTMLResponse)
def sensors_page(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return render(
        request, "sensors.html", sensors=sensor_lib.list_sensors(settings, executor)
    )


@router.get("/ajax/sensors")
def sensors_list(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse(
        {"ok": True, "sensors": sensor_lib.list_sensors(settings, executor)}
    )


@router.get("/ajax/sensors/read-all")
def sensors_read_all(request: Request):
    settings = request.app.state.settings
    executor = get_executor(settings)
    return JSONResponse({"ok": True, "sensors": sensor_lib.read_all(settings, executor)})


@router.get("/ajax/sensors/read")
def sensors_read(request: Request, id: str = ""):
    settings = request.app.state.settings
    executor = get_executor(settings)
    try:
        sensor = sensor_lib.read_sensor(settings, executor, id)
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
    return JSONResponse({"ok": True, "sensor": sensor})
