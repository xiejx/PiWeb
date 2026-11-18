from __future__ import annotations

import json
from functools import lru_cache

from fastapi import Request

from .config import I18N_DIR, Settings

SUPPORTED_LANGS: dict[str, str] = {
    "en": "English",
    "de": "Deutsch",
    "uk": "Українська",
    "es": "Español",
    "fr": "Français",
}


@lru_cache(maxsize=8)
def load_strings(lang: str) -> dict[str, str]:
    strings: dict[str, str] = {}
    english = I18N_DIR / "en.json"
    if english.is_file():
        strings.update(json.loads(english.read_text(encoding="utf-8")))
    if lang != "en":
        override = I18N_DIR / f"{lang}.json"
        if override.is_file():
            strings.update(json.loads(override.read_text(encoding="utf-8")))
    return strings


def current_lang(request: Request, settings: Settings) -> str:
    session_lang = request.session.get("lang")
    if session_lang in SUPPORTED_LANGS:
        return session_lang
    cookie_lang = request.cookies.get("piweb_lang", "")
    if cookie_lang in SUPPORTED_LANGS:
        return cookie_lang
    return settings.default_lang if settings.default_lang in SUPPORTED_LANGS else "en"


def translator(lang: str):
    strings = load_strings(lang)

    def t(key: str, default: str | None = None) -> str:
        value = strings.get(key)
        if value:
            return value
        return default if default is not None else key

    return t
