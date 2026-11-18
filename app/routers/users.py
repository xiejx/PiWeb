from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..templating import render

router = APIRouter()

PASSWD_PATH = "/etc/passwd"
GROUP_PATH = "/etc/group"


def _number(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0


def parse_passwd(path: str = PASSWD_PATH) -> list[dict]:
    """Read /etc/passwd; a missing or unreadable file yields an empty list."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    users: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 7:
            continue
        users.append(
            {
                "name": fields[0],
                "uid": _number(fields[2]),
                "gid": _number(fields[3]),
                "comment": fields[4].strip(),
                "home": fields[5],
                "shell": fields[6],
            }
        )
    users.sort(key=lambda user: user["uid"])
    return users


def parse_group(path: str = GROUP_PATH) -> list[dict]:
    """Read /etc/group; a missing or unreadable file yields an empty list."""
    try:
        raw = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    groups: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split(":")
        if len(fields) < 4:
            continue
        groups.append(
            {
                "name": fields[0],
                "gid": _number(fields[2]),
                "members": [member.strip() for member in fields[3].split(",") if member.strip()],
            }
        )
    groups.sort(key=lambda group: group["gid"])
    return groups


def is_login_user(user: dict) -> bool:
    """A human account: uid 1000-59999 with a real login shell."""
    return (
        1000 <= user["uid"] < 60000
        and not user["shell"].endswith("nologin")
        and not user["shell"].endswith("false")
    )


@router.get("/users", response_class=HTMLResponse)
def users_page(request: Request):
    users = parse_passwd()
    groups = parse_group()
    return render(
        request,
        "users.html",
        users=users,
        login_users=[user for user in users if is_login_user(user)],
        groups=groups,
    )
