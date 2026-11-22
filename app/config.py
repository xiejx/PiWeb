from __future__ import annotations

import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - old Raspberry Pi OS
    import tomli as tomllib  # type: ignore


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
STATIC_DIR = APP_DIR / "static"
TEMPLATES_DIR = APP_DIR / "templates"
I18N_DIR = APP_DIR / "i18n"

DEFAULT_MODULES: dict[str, dict[str, Any]] = {
    "services": {"title": "Services", "enabled": True},
    "processes": {"title": "Processes", "enabled": True},
    "actions": {"title": "Actions", "enabled": True},
    "gpio": {"title": "GPIO", "enabled": True},
    "buttons": {"title": "Buttons", "enabled": True},
    "links": {"title": "Web Navigation", "enabled": True},
    "rpi": {"title": "Raspberry Pi", "enabled": True},
    "sensors": {"title": "Sensors", "enabled": True},
    "wifi": {"title": "Wi-Fi", "enabled": True},
    "docker": {"title": "Docker", "enabled": False},
    "packages": {"title": "Packages", "enabled": True, "group": "System"},
    "logs": {"title": "Logs", "enabled": True, "group": "System"},
    "cron": {"title": "Cron", "enabled": True, "group": "System"},
    "users": {"title": "Users", "enabled": True, "group": "System"},
    "scripts": {"title": "Scripts", "enabled": True, "group": "System"},
    "backup": {"title": "Backup", "enabled": True, "group": "System"},
    "check": {"title": "System Check", "enabled": True, "group": "System"},
    "tehybug": {
        "title": "TeHyBug",
        "enabled": False,
        "url": "/modules/tehybug",
        "iframe": False,
    },
    "button_api": {"title": "Button API", "enabled": False, "no_nav": True},
}


@dataclass
class ModuleConfig:
    key: str
    title: str
    enabled: bool = True
    group: str = ""
    url: str = ""
    iframe: bool = False
    no_nav: bool = False


@dataclass
class DockerConfig:
    """Settings for the Docker module ([docker] in config.toml).

    PiWeb talks to the daemon through the command line client. `privileged`
    picks how: "group" assumes the service user is in the docker group, "sudo"
    goes through the sudoers entry and works on older installations.
    """

    privileged: str = "group"  # group | sudo
    compose_enabled: bool = True
    compose_dirs: list[str] = field(default_factory=list)
    stats_enabled: bool = True
    log_max_lines: int = 2000
    task_timeout: int = 1800


@dataclass
class Settings:
    # server
    host: str = "127.0.0.1"
    port: int = 8000
    root_path: str = ""
    trusted_proxy: bool = True
    # execution
    execution_mode: str = "local"  # local | ssh
    ssh_host: str = "localhost"
    ssh_port: int = 22
    ssh_user: str = "pi"
    ssh_password: str = "raspberry"
    # auth
    login_required: bool = True
    login_user: str = "pi"
    login_pass: str = "raspberry"
    login_check_system_user: bool = False
    login_totp_secret: str = ""
    allow_unauthenticated: bool = False
    update_key: str = ""
    basic_auth: bool = False
    basic_auth_user: str = "api"
    basic_auth_pass: str = "secret"
    # session / throttle
    session_idle_timeout: int = 3600
    session_absolute_timeout: int = 43200
    session_cookie_secure: bool = False
    login_max_failures: int = 5
    login_failure_window: int = 900
    login_lockout_time: int = 900
    # alerts
    alert_ntfy_url: str = ""
    alert_telegram_token: str = ""
    alert_telegram_chat: str = ""
    alert_temp_threshold: int = 75
    alert_disk_threshold: int = 95
    # ui
    default_lang: str = "zh"
    dashboard_services: list[str] = field(
        default_factory=lambda: ["ssh", "nginx", "cron"]
    )
    api_allow_ips: list[str] = field(default_factory=list)
    debug: bool = False
    # modules
    modules: dict[str, ModuleConfig] = field(default_factory=dict)
    # docker
    docker: DockerConfig = field(default_factory=DockerConfig)
    # raw TOML data for optional feature sections ([sensors], [storage], …)
    raw_config: dict[str, Any] = field(default_factory=dict)
    # paths
    config_path: Path | None = None
    data_dir: Path = BASE_DIR / "data"
    buttons_dir: Path = BASE_DIR / "buttons"
    command_logs_dir: Path = BASE_DIR / "command_logs"
    scripts_dir: Path = BASE_DIR / "user_scripts"
    backups_dir: Path = BASE_DIR / "data" / "backups"
    session_secret: str = ""

    @property
    def active_modules(self) -> list[ModuleConfig]:
        return [m for m in self.modules.values() if m.enabled and not m.no_nav]

    def module(self, key: str) -> ModuleConfig | None:
        return self.modules.get(key)

    def module_enabled(self, key: str) -> bool:
        mod = self.modules.get(key)
        return bool(mod and mod.enabled)

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.buttons_dir,
            self.command_logs_dir,
            self.scripts_dir,
            self.backups_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    return value if isinstance(value, dict) else {}


def _pick(section: dict[str, Any], key: str, default: Any) -> Any:
    value = section.get(key, default)
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int) and not isinstance(default, bool):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default
    if isinstance(default, list):
        return list(value) if isinstance(value, (list, tuple)) else default
    if isinstance(default, str):
        return str(value) if value is not None else default
    return value if value is not None else default


def _parse_modules(data: dict[str, Any]) -> dict[str, ModuleConfig]:
    modules: dict[str, ModuleConfig] = {}
    configured = data.get("modules", {})
    if not isinstance(configured, dict) or not configured:
        configured = {}

    # Start from defaults so the navbar stays complete, honouring config overrides.
    for key, defaults in DEFAULT_MODULES.items():
        raw = configured.get(key, {})
        raw = raw if isinstance(raw, dict) else {}
        modules[key] = ModuleConfig(
            key=key,
            title=str(raw.get("title", defaults.get("title", key))),
            enabled=bool(raw.get("enabled", defaults.get("enabled", True))),
            group=str(raw.get("group", defaults.get("group", ""))),
            url=str(raw.get("url", defaults.get("url", ""))),
            iframe=bool(raw.get("iframe", defaults.get("iframe", False))),
            no_nav=bool(raw.get("no_nav", defaults.get("no_nav", False))),
        )

    # Allow extra third-party modules defined only in the config file.
    for key, raw in configured.items():
        if key in modules or not isinstance(raw, dict):
            continue
        modules[key] = ModuleConfig(
            key=key,
            title=str(raw.get("title", key)),
            enabled=bool(raw.get("enabled", False)),
            group=str(raw.get("group", "")),
            url=str(raw.get("url", "")),
            iframe=bool(raw.get("iframe", False)),
            no_nav=bool(raw.get("no_nav", False)),
        )
    return modules


def _parse_docker(data: dict[str, Any]) -> DockerConfig:
    section = _section(data, "docker")
    privileged = str(section.get("privileged", "group")).strip().lower()
    if privileged not in {"group", "sudo"}:
        privileged = "group"

    raw_dirs = section.get("compose_dirs", [])
    compose_dirs = (
        [str(item) for item in raw_dirs if str(item).strip()]
        if isinstance(raw_dirs, (list, tuple))
        else []
    )

    def _int(key: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(section.get(key, default))))
        except (TypeError, ValueError):
            return default

    return DockerConfig(
        privileged=privileged,
        compose_enabled=bool(section.get("compose_enabled", True)),
        compose_dirs=compose_dirs,
        stats_enabled=bool(section.get("stats_enabled", True)),
        log_max_lines=_int("log_max_lines", 2000, 10, 5000),
        task_timeout=_int("task_timeout", 1800, 60, 7200),
    )


def load_settings(path: str | os.PathLike[str] | None = None) -> Settings:
    config_path = Path(
        path or os.environ.get("PIWEB_CONFIG") or BASE_DIR / "config.toml"
    )
    data: dict[str, Any] = {}
    if config_path.is_file():
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)

    server = _section(data, "server")
    execution = _section(data, "execution")
    ssh = _section(data, "ssh")
    auth = _section(data, "auth")
    basic = _section(auth, "basic")
    session = _section(data, "session")
    throttle = _section(data, "login_throttle")
    alerts = _section(data, "alerts")
    ui = _section(data, "ui")

    settings = Settings(
        host=_pick(server, "host", Settings.host),
        port=_pick(server, "port", Settings.port),
        root_path=_pick(server, "root_path", Settings.root_path),
        trusted_proxy=_pick(server, "trusted_proxy", True),
        execution_mode=_pick(execution, "mode", "local"),
        ssh_host=_pick(ssh, "host", "localhost"),
        ssh_port=_pick(ssh, "port", 22),
        ssh_user=_pick(ssh, "user", "pi"),
        ssh_password=_pick(ssh, "password", "raspberry"),
        login_required=_pick(auth, "required", True),
        login_user=_pick(auth, "user", _pick(ssh, "user", "pi")),
        login_pass=_pick(auth, "password", _pick(ssh, "password", "raspberry")),
        login_check_system_user=_pick(auth, "check_system_user", False),
        login_totp_secret=_pick(auth, "totp_secret", ""),
        allow_unauthenticated=_pick(auth, "allow_unauthenticated", False),
        update_key=_pick(auth, "update_key", ""),
        basic_auth=_pick(basic, "enabled", False),
        basic_auth_user=_pick(basic, "user", "api"),
        basic_auth_pass=_pick(basic, "password", "secret"),
        session_idle_timeout=_pick(session, "idle_timeout", 3600),
        session_absolute_timeout=_pick(session, "absolute_timeout", 43200),
        session_cookie_secure=_pick(session, "cookie_secure", False),
        login_max_failures=_pick(throttle, "max_failures", 5),
        login_failure_window=_pick(throttle, "window", 900),
        login_lockout_time=_pick(throttle, "lockout", 900),
        alert_ntfy_url=_pick(alerts, "ntfy_url", ""),
        alert_telegram_token=_pick(alerts, "telegram_token", ""),
        alert_telegram_chat=_pick(alerts, "telegram_chat", ""),
        alert_temp_threshold=_pick(alerts, "temp_threshold", 75),
        alert_disk_threshold=_pick(alerts, "disk_threshold", 95),
        default_lang=_pick(ui, "lang", "zh"),
        dashboard_services=_pick(ui, "dashboard_services", ["ssh", "nginx", "cron"]),
        api_allow_ips=_pick(ui, "api_allow_ips", []),
        debug=_pick(ui, "debug", False),
        modules=_parse_modules(data),
        docker=_parse_docker(data),
        raw_config=data,
        config_path=config_path,
        data_dir=Path(os.environ.get("PIWEB_DATA_DIR", BASE_DIR / "data")),
        buttons_dir=BASE_DIR / "buttons",
        command_logs_dir=BASE_DIR / "command_logs",
        scripts_dir=BASE_DIR / "user_scripts",
        backups_dir=Path(
            os.environ.get("PIWEB_DATA_DIR", BASE_DIR / "data")
        )
        / "backups",
    )
    settings.ensure_dirs()
    from .module_state import apply as apply_module_state

    apply_module_state(settings)
    settings.session_secret = _load_session_secret(settings)
    return settings


def _load_session_secret(settings: Settings) -> str:
    env_secret = os.environ.get("PIWEB_SESSION_SECRET", "")
    if env_secret:
        return env_secret
    secret_file = settings.data_dir / "session_secret"
    if secret_file.is_file():
        value = secret_file.read_text(encoding="ascii", errors="ignore").strip()
        if value:
            return value
    value = secrets.token_urlsafe(48)
    secret_file.write_text(value + "\n", encoding="ascii")
    try:
        secret_file.chmod(0o600)
    except OSError:
        pass
    return value


def save_auth_credentials(path: Path, user: str, password_hash: str) -> None:
    """Persist first-run credentials back into config.toml.

    Rewrites only the [auth] user/password lines, creating the file (and the
    section) when needed, so unrelated configuration is preserved.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if not text.strip():
        text = (
            "# PiWeb configuration — generated by first-run setup.\n"
            "[auth]\n"
            "required = true\n"
            f'user = "{user}"\n'
            f'password = "{password_hash}"\n'
        )
    else:
        if "[auth]" not in text:
            text = text.rstrip() + "\n\n[auth]\nrequired = true\n"
        section_re = re.compile(r"(?ms)^\[auth\]\s*$(.*?)(?=^\[|\Z)")
        match = section_re.search(text)
        assert match is not None
        body = match.group(1)
        body = re.sub(
            r"(?m)^\s*user\s*=.*$", "", body
        )
        body = re.sub(
            r"(?m)^\s*password\s*=.*$", "", body
        )
        body = body.rstrip("\n")
        replacement = (
            "\n"
            f'user = "{user}"\n'
            f'password = "{password_hash}"\n'
        )
        text = text[: match.start(1)] + body + replacement + text[match.end(1) :]

    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
