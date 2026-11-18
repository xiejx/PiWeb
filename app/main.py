from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .config import STATIC_DIR, load_settings
from .security import AuthGateMiddleware, require_csrf
from .templating import render

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("piweb")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    settings.ensure_dirs()
    logger.info(
        "PiWeb %s starting — execution=%s, login_required=%s, config=%s",
        __version__,
        settings.execution_mode,
        settings.login_required,
        settings.config_path,
    )
    if settings.login_pass == "raspberry" and settings.login_required:
        logger.warning("Default login is still configured — change it or run /setup.")
    yield
    logger.info("PiWeb stopped")


def create_app(settings=None) -> FastAPI:
    settings = settings or load_settings()

    app = FastAPI(
        title="PiWeb",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    from .routers import (
        actions,
        auth,
        backup,
        buttons,
        check,
        cron,
        dashboard,
        docker,
        gpio,
        logs,
        modules,
        packages,
        processes,
        rpi,
        scripts,
        sensors,
        services,
        setup_api,
        tehybug,
        update,
        users,
        wifi,
        api,
    )

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    protected = [Depends(require_csrf)]
    app.include_router(auth.router, dependencies=protected)
    app.include_router(dashboard.router, dependencies=protected)
    app.include_router(services.router, dependencies=protected)
    app.include_router(processes.router, dependencies=protected)
    app.include_router(actions.router, dependencies=protected)
    app.include_router(gpio.router, dependencies=protected)
    app.include_router(buttons.router, dependencies=protected)
    app.include_router(scripts.router, dependencies=protected)
    app.include_router(logs.router, dependencies=protected)
    app.include_router(cron.router, dependencies=protected)
    app.include_router(packages.router, dependencies=protected)
    app.include_router(users.router, dependencies=protected)
    app.include_router(docker.router, dependencies=protected)
    app.include_router(rpi.router, dependencies=protected)
    app.include_router(sensors.router, dependencies=protected)
    app.include_router(wifi.router, dependencies=protected)
    app.include_router(check.router, dependencies=protected)
    app.include_router(backup.router, dependencies=protected)
    app.include_router(update.router, dependencies=protected)
    app.include_router(setup_api.router, dependencies=protected)
    app.include_router(modules.router, dependencies=protected)
    # TeHyBug keeps its device reporting endpoint outside the CSRF dependency.
    app.include_router(tehybug.router, dependencies=protected)
    app.include_router(tehybug.public_router)
    # The public button API authenticates with per-button hashes, not sessions.
    app.include_router(api.router)

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "app": "piweb",
            "version": __version__,
            "execution": settings.execution_mode,
        }

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        wants_json = (
            request.headers.get("hx-request") == "true"
            or "application/json" in request.headers.get("accept", "")
            or request.url.path.startswith(("/api/", "/ajax/"))
        )
        if wants_json:
            return JSONResponse(
                {"ok": False, "error": exc.detail}, status_code=exc.status_code
            )
        if exc.status_code == 401:
            from fastapi.responses import RedirectResponse

            return RedirectResponse("/login", status_code=303)
        return render(
            request,
            "error.html",
            status_code=exc.status_code,
            message=str(exc.detail),
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "frame-src 'self' http: https:; "
            "object-src 'none'; base-uri 'self'",
        )
        if request.url.path.startswith("/static/"):
            response.headers.setdefault(
                "Cache-Control", "public, max-age=86400, immutable"
            )
        return response

    # Added last → outermost: the session must exist before the gate reads it.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(AuthGateMiddleware)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="piweb_session",
        max_age=settings.session_absolute_timeout or 43200,
        same_site="lax",
        https_only=settings.session_cookie_secure,
    )
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    settings = app.state.settings
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
