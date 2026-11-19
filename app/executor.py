from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import Settings


@dataclass
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output": self.output,
        }


class Executor:
    """Run shell commands locally or over SSH to localhost.

    PiWeb historically authenticated over SSH as the Pi's admin account. The
    FastAPI rewrite runs as a systemd service and defaults to direct local
    execution; setting `execution.mode = "ssh"` restores the old behaviour.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def mode(self) -> str:
        return self.settings.execution_mode

    def run(
        self,
        command: str,
        timeout: float = 30.0,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        input: str | None = None,
    ) -> CommandResult:
        if self.mode == "ssh":
            return self._run_ssh(command, timeout, input)
        return self._run_local(command, timeout, cwd, env, input)

    def _run_local(
        self,
        command: str,
        timeout: float,
        cwd: str | None,
        env: dict[str, str] | None,
        input: str | None = None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                cwd=cwd,
                env=env,
                input=input,
            )
            return CommandResult(
                stdout=completed.stdout or "",
                stderr=completed.stderr or "",
                returncode=completed.returncode,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(stderr=f"Command timed out after {timeout}s", returncode=124)
        except OSError as exc:
            return CommandResult(stderr=str(exc), returncode=127)

    def _run_ssh(
        self, command: str, timeout: float, input: str | None = None
    ) -> CommandResult:
        try:
            import paramiko
        except ImportError as exc:  # pragma: no cover - dependency is required
            return CommandResult(stderr=f"paramiko missing: {exc}", returncode=127)

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=self.settings.ssh_host,
                port=self.settings.ssh_port,
                username=self.settings.ssh_user,
                password=self.settings.ssh_password,
                timeout=min(timeout, 10),
                allow_agent=False,
                look_for_keys=False,
            )
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            if input:
                stdin.write(input)
                stdin.flush()
            stdin.channel.shutdown_write()
            out = stdout.read().decode("utf-8", "replace")
            err = stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
            return CommandResult(stdout=out, stderr=err, returncode=code)
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI
            return CommandResult(stderr=f"SSH error: {exc}", returncode=255)
        finally:
            client.close()

    def run_background(self, command: str, log_path: Path) -> CommandResult:
        """Start a command detached, its output appended to log_path."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        quoted_log = shlex.quote(str(log_path))
        full = f"({command}) >> {quoted_log} 2>&1 &"
        if self.mode == "ssh":
            result = self._run_ssh(full, timeout=15)
        else:
            try:
                subprocess.Popen(
                    full,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                result = CommandResult(stdout="started")
            except OSError as exc:
                result = CommandResult(stderr=str(exc), returncode=127)
        if not log_path.exists():
            log_path.write_text(
                f"Command started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
                encoding="utf-8",
            )
        return result

    def command_exists(self, name: str) -> bool:
        if self.mode == "ssh":
            return self._run_ssh(f"command -v {shlex.quote(name)}", 10).ok
        return shutil.which(name) is not None

    def write_file(self, path: Path, content: str, executable: bool = False) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if executable:
            try:
                path.chmod(0o755)
            except OSError:
                pass


def get_executor(settings: Settings) -> Executor:
    return Executor(settings)


def safe_command(command: str) -> bool:
    """Reject obviously broken input while keeping the panel a real shell."""
    return bool(command.strip()) and "\x00" not in command and len(command) <= 5000


def which(name: str) -> bool:
    return shutil.which(name) is not None


def read_text(path: str, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return default


def format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num) < 1024.0:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} PB"


def format_duration(seconds: float) -> str:
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def environment_ok() -> bool:
    return os.name == "posix" and Path("/proc").is_dir()
