"""Internacionalización es/en. Prioridad: Accept-Language > locale del usuario > DEFAULT_LOCALE."""
from __future__ import annotations

import json
from functools import lru_cache

from app.core.config import get_settings


@lru_cache
def catalogs() -> dict[str, dict]:
    s = get_settings()
    base = s.path(s.I18N_DIR)
    return {loc: json.loads((base / f"{loc}.json").read_text(encoding="utf-8")) for loc in s.locales}


def negotiate(accept_language: str | None, user_locale: str | None = None) -> str:
    s = get_settings()
    if accept_language:
        for part in accept_language.split(","):
            tag = part.split(";")[0].strip().lower()
            primary = tag.split("-")[0]
            if primary in s.locales:
                return primary
    if user_locale in s.locales:
        return user_locale  # type: ignore[return-value]
    return s.DEFAULT_LOCALE


def t(key: str, locale: str) -> str:
    node: object = catalogs().get(locale) or catalogs()[get_settings().DEFAULT_LOCALE]
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return key
        node = node[part]
    return node if isinstance(node, str) else key
