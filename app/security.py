from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import struct
import time
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import Settings
from .i18n import SUPPORTED_LANGS

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
PUBLIC_PREFIXES = (
    "/static/",
    "/login",
    "/logout",
    "/setup",
    "/health",
    "/api/",
    "/update",
    "/modules/tehybug/track",
)

PBKDF2_ITERATIONS = 260_000

# Passwords that ship with PiWeb / are used as placeholders. Refuse them when
# a user picks a password so an install is never left with a known credential.
WEAK_PASSWORDS = {"raspberry", "password", "secret", "piweb", "admin"}


# ── Passwords ────────────────────────────────────────────────────────────────

def password_policy_errors(password: str) -> list[str]:
    """Return validation messages for a proposed password (empty means OK)."""
    errors: list[str] = []
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    if password.lower() in WEAK_PASSWORDS:
        errors.append(
            "That password is one of the well-known defaults — choose another."
        )
    return errors


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("ascii"), PBKDF2_ITERATIONS
    )
    encoded = base64.b64encode(digest).decode("ascii")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${encoded}"


def verify_password(stored: str, given: str) -> bool:
    """Check a pbkdf2 hash, or compare cleartext when that is what is stored."""
    if not stored or not given:
        return False
    stored = stored.strip()
    if stored.startswith("pbkdf2_sha256$"):
        try:
            _, iterations, salt, encoded = stored.split("$", 3)
            digest = hashlib.pbkdf2_hmac(
                "sha256",
                given.encode("utf-8"),
                salt.encode("ascii"),
                int(iterations),
            )
            return hmac.compare_digest(
                base64.b64encode(digest).decode("ascii"), encoded
            )
        except (ValueError, TypeError):
            return False
    return hmac.compare_digest(stored, given)


def auth_configured(settings: Settings) -> bool:
    """True once real credentials exist (the shipped defaults do not count)."""
    if settings.login_required:
        if settings.login_check_system_user:
            return True
        if settings.login_pass and settings.login_pass != "raspberry":
            return True
    if (
        settings.basic_auth
        and settings.basic_auth_pass
        and settings.basic_auth_pass != "secret"
    ):
        return True
    return False


def open_mode(settings: Settings) -> bool:
    return settings.allow_unauthenticated


def default_credentials(settings: Settings) -> bool:
    if (
        settings.login_required
        and not settings.login_check_system_user
        and settings.login_pass == "raspberry"
    ):
        return True
    if settings.basic_auth and settings.basic_auth_pass == "secret":
        return True
    return False


# ── TOTP (RFC 6238) ──────────────────────────────────────────────────────────

B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"


def base32_decode(value: str) -> bytes:
    value = "".join(ch for ch in value.upper() if ch in B32_ALPHABET)
    bits = "".join(
        bin(B32_ALPHABET.index(ch))[2:].zfill(5) for ch in value
    )
    return bytes(
        int(bits[i : i + 8], 2) for i in range(0, len(bits) - 7, 8)
    )


def base32_encode(raw: bytes) -> str:
    bits = "".join(bin(byte)[2:].zfill(8) for byte in raw)
    padding = (-len(bits)) % 5
    bits += "0" * padding
    return "".join(
        B32_ALPHABET[int(bits[i : i + 5], 2)] for i in range(0, len(bits), 5)
    )


def totp_new_secret() -> str:
    return base32_encode(secrets.token_bytes(20))


def totp_code(secret_b32: str, counter: int) -> str:
    key = base32_decode(secret_b32)
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[19] & 0x0F
    number = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(number % 1_000_000).zfill(6)


def totp_match(secret_b32: str, code: str, now: int | None = None) -> int:
    code = "".join(code.split())
    if not (len(code) == 6 and code.isdigit()):
        return 0
    step = int((now if now is not None else time.time()) // 30)
    for drift in (0, -1, 1):
        if hmac.compare_digest(totp_code(secret_b32, step + drift), code):
            return step + drift
    return 0


def totp_verify_login(settings: Settings, code: str) -> bool:
    if not settings.login_totp_secret:
        return True
    counter = totp_match(settings.login_totp_secret, code)
    if counter == 0:
        return False
    last_file = settings.command_logs_dir / ".totp_last"
    try:
        last = int(last_file.read_text(encoding="ascii").strip() or 0)
    except (OSError, ValueError):
        last = 0
    if counter <= last:
        return False
    last_file.write_text(str(counter), encoding="ascii")
    return True


def totp_provisioning_uri(settings: Settings, secret: str) -> str:
    label = f"PiWeb:{settings.login_user}"
    return (
        "otpauth://totp/"
        f"{label}?secret={secret}&issuer=PiWeb&algorithm=SHA1&digits=6&period=30"
    )


# ── Authentication log ───────────────────────────────────────────────────────

def auth_log(settings: Settings, event: str, detail: str = "", ip: str = "",
             agent: str = "") -> None:
    path = settings.command_logs_dir / "auth.log"
    if not path.parent.is_dir():
        return
    clean = lambda v: str(v).replace("\t", " ").replace("\n", " ").replace("\r", " ")
    line = "\t".join(
        [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            clean(event),
            clean(ip) or "-",
            clean(detail) or "-",
            clean(agent)[:120] or "-",
        ]
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    try:
        if path.stat().st_size > 200_000:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if len(lines) > 500:
                path.write_text("\n".join(lines[-500:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def auth_log_recent(settings: Settings, limit: int = 10) -> list[dict[str, str]]:
    path = settings.command_logs_dir / "auth.log"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    events = []
    for line in reversed(lines[-200:]):
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        events.append(
            {
                "time": fields[0],
                "event": fields[1],
                "ip": fields[2],
                "detail": fields[3] if len(fields) > 3 else "",
                "agent": fields[4] if len(fields) > 4 else "",
            }
        )
        if len(events) >= limit:
            break
    return events


# ── Login throttling ─────────────────────────────────────────────────────────

def _throttle_path(settings: Settings) -> Path:
    return settings.command_logs_dir / ".login_attempts.json"


def _throttle_load(settings: Settings) -> dict:
    try:
        data = json.loads(_throttle_path(settings).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _throttle_save(settings: Settings, data: dict) -> None:
    path = _throttle_path(settings)
    if path.parent.is_dir():
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)


def login_locked_for(settings: Settings, ip: str) -> int:
    if not ip:
        return 0
    entry = _throttle_load(settings).get(ip, {})
    count = int(entry.get("count", 0))
    last = int(entry.get("last", 0))
    if count < settings.login_max_failures:
        return 0
    remaining = (last + settings.login_lockout_time) - int(time.time())
    return remaining if remaining > 0 else 0


def record_login_failure(settings: Settings, ip: str) -> None:
    if not ip:
        return
    now = int(time.time())
    data = _throttle_load(settings)
    ttl = max(settings.login_failure_window, settings.login_lockout_time) * 2
    for key in list(data):
        if now - int(data[key].get("last", 0)) > ttl:
            del data[key]
    entry = data.get(ip, {})
    count = int(entry.get("count", 0))
    if now - int(entry.get("last", 0)) > settings.login_failure_window:
        count = 0
    data[ip] = {"count": count + 1, "last": now}
    _throttle_save(settings, data)


def clear_login_failures(settings: Settings, ip: str) -> None:
    data = _throttle_load(settings)
    if ip in data:
        del data[ip]
        _throttle_save(settings, data)


# ── Client address / IP allow-list ───────────────────────────────────────────

def client_ip(request: Request, settings: Settings | None = None) -> str:
    peer = request.client.host if request.client else ""
    settings = settings or getattr(request.app.state, "settings", None)
    trusted = bool(settings and settings.trusted_proxy)
    if trusted and peer in {"127.0.0.1", "::1", "localhost"}:
        real = request.headers.get("x-real-ip", "").strip()
        if real:
            return real
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return peer


def is_local_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def cidr_match(ip: str, cidr: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False)
    except ValueError:
        return False


def ip_allowed(ip: str, allow: list[str]) -> bool:
    entries = [str(e).strip() for e in allow if str(e).strip()]
    if not entries:
        return True
    for entry in entries:
        if "/" in entry:
            if cidr_match(ip, entry):
                return True
        elif ip == entry:
            return True
    return False


# ── Session state ────────────────────────────────────────────────────────────

def session_authenticated(request: Request, settings: Settings) -> bool:
    session = request.session
    signed_in = False
    if session.get("sys_user") and settings.login_check_system_user:
        signed_in = True
    elif session.get("user_hash") and hmac.compare_digest(
        session["user_hash"], hashlib.md5(settings.login_user.encode()).hexdigest()
    ):
        signed_in = True
    if not signed_in:
        return False
    return not session_expired(request, settings)


def session_expired(request: Request, settings: Settings) -> bool:
    session = request.session
    now = int(time.time())
    if not session.get("auth_time"):
        session["auth_time"] = now
    if not session.get("last_seen"):
        session["last_seen"] = now

    idle = settings.session_idle_timeout
    absolute = settings.session_absolute_timeout
    expired = (idle > 0 and now - int(session["last_seen"]) > idle) or (
        absolute > 0 and now - int(session["auth_time"]) > absolute
    )
    if expired:
        end_session(request)
        session["just_expired"] = True
        return True
    if not request.headers.get("x-piweb-background"):
        session["last_seen"] = now
    return False


def mark_login(request: Request, user: str, mode: str) -> None:
    now = int(time.time())
    request.session.clear()
    request.session["csrf"] = secrets.token_urlsafe(32)
    if mode == "system":
        request.session["sys_user"] = user
    else:
        request.session["user_hash"] = hashlib.md5(
            user.encode("utf-8")
        ).hexdigest()
    request.session["auth_time"] = now
    request.session["last_seen"] = now


def end_session(request: Request) -> None:
    for key in ("sys_user", "user_hash", "auth_time", "last_seen"):
        request.session.pop(key, None)


def csrf_token(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


async def require_csrf(request: Request) -> None:
    """Validate the CSRF token on unsafe methods.

    Installed as a router-level dependency, so every POST is covered. Tokens
    arrive either as the X-CSRF-Token header (HTMX / fetch) or a form field.
    """
    if request.method in SAFE_METHODS:
        return
    if request.url.path.startswith("/api/"):
        return
    expected = request.session.get("csrf", "")
    supplied = request.headers.get("x-csrf-token", "")
    if not supplied:
        content_type = request.headers.get("content-type", "")
        if "form" in content_type:
            try:
                # FastAPI has already parsed form bodies for endpoints that
                # declare Form/File parameters, so this reads the cache.
                form = await request.form()
                supplied = str(form.get("csrf_token", ""))
            except Exception:  # noqa: BLE001 - malformed body
                supplied = ""
    if not expected or not supplied or not hmac.compare_digest(expected, supplied):
        wants_json = (
            request.headers.get("hx-request") == "true"
            or "application/json" in request.headers.get("accept", "")
            or request.url.path.startswith("/ajax/")
        )
        if wants_json:
            raise HTTPException(status_code=403, detail="CSRF token mismatch")
        raise HTTPException(status_code=403, detail="CSRF token mismatch")


# ── Request gate middleware ──────────────────────────────────────────────────

class AuthGateMiddleware:
    """Fail-closed access gate, language switch, and Basic Auth support."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        settings: Settings = request.app.state.settings
        path = request.url.path
        session = request.session

        # Language switch (?lang=xx) persists to session and a long-lived cookie.
        lang = request.query_params.get("lang")
        if lang in SUPPORTED_LANGS:
            session["lang"] = lang
            scope.setdefault("state", {})["piweb_set_lang_cookie"] = lang

        if path.startswith(PUBLIC_PREFIXES):
            response = await self._call(scope, receive, send, settings, request)
            return

        authenticated = session_authenticated(request, settings)
        if not authenticated and settings.basic_auth:
            authenticated = self._check_basic_auth(request, settings)

        if not authenticated:
            if not auth_configured(settings):
                if open_mode(settings):
                    await self._call(scope, receive, send, settings, request)
                    return
                await self._redirect_to_setup(scope, receive, send, request)
                return

            locked = login_locked_for(settings, client_ip(request, settings))
            if locked:
                response = PlainTextResponse(
                    f"Too many failed attempts. Try again in about {max(1, locked // 60)} minute(s).\n",
                    status_code=429,
                    headers={"Retry-After": str(locked)},
                )
                await response(scope, receive, send)
                return

            if settings.basic_auth and not settings.login_required:
                response = PlainTextResponse(
                    "401 Unauthorized",
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="PiWeb"'},
                )
                await response(scope, receive, send)
                return

            wants_json = (
                request.headers.get("hx-request") == "true"
                or path.startswith("/ajax/")
                or "application/json" in request.headers.get("accept", "")
            )
            if wants_json:
                response = JSONResponse(
                    {"ok": False, "error": "authentication required"},
                    status_code=401,
                    headers={"HX-Redirect": "/login"},
                )
            else:
                target = "/login"
                if session.get("just_expired"):
                    target += "?action=session_expired"
                response = RedirectResponse(target, status_code=303)
            await response(scope, receive, send)
            return

        await self._call(scope, receive, send, settings, request)

    async def _call(self, scope, receive, send, settings, request):
        lang = scope.get("state", {}).get("piweb_set_lang_cookie")
        if not lang:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.append(
                    "set-cookie",
                    f"piweb_lang={lang}; Path=/; Max-Age=31536000; SameSite=Lax",
                )
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    async def _redirect_to_setup(self, scope, receive, send, request):
        response = RedirectResponse("/setup", status_code=303)
        await response(scope, receive, send)

    def _check_basic_auth(self, request: Request, settings: Settings) -> bool:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("basic "):
            return False
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, password = decoded.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        ip = client_ip(request, settings)
        if login_locked_for(settings, ip):
            return False
        if hmac.compare_digest(settings.basic_auth_user, user) and verify_password(
            settings.basic_auth_pass, password
        ):
            clear_login_failures(settings, ip)
            return True
        record_login_failure(settings, ip)
        return False
