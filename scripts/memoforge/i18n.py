"""Language packs: the English floor in code, the other four in `lib/i18n/*.json` (D-168).

English strings live in `i18n_en.EN` — always imported, the floor for delivery including
`finalize --salvage`. The other four languages ship the same key tree (`memo` and `ui`
both required) as JSON packs. Lookup rules: a missing key answers from EN without an
event; a `memo.*` key of an unreadable pack raises `PackUnavailable`, a `ui.*` key of
an unreadable pack answers from EN (plan 56 falls back to English for the interface).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import i18n_en, pylauncher

LANGUAGES: tuple[str, ...] = ("en", "de", "fr", "es", "ru")
"""The one language set for the memo and the interface (D-169)."""

LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "es": "Spanish",
    "ru": "Russian",
}

DEFAULT = "en"
"""Fallback when nothing usable was requested (D-169)."""

AUTO = "auto"
"""Take `--detected-language` (D-170); only an option value, never a named flag."""

PACK_DIR: Path = pylauncher.plugin_root() / "lib" / "i18n"
"""Where the four JSON packs live; tests patch this attribute with a temp dir."""

_cache: dict[str, dict] = {}
"""Loaded packs by resolved file path — a temp `PACK_DIR` never collides (D-168)."""


class PackUnavailable(RuntimeError):
    """A `memo` language pack that cannot be read; `.code` is the language code."""

    def __init__(self, code: str) -> None:
        super().__init__(f"language_pack_unavailable: {code}")
        self.code = code


def normalize(code: object) -> str | None:
    """`"RU "` -> `"ru"`; anything outside `LANGUAGES` (incl. None) -> None."""
    if not isinstance(code, str):
        return None
    text = code.strip().lower()
    return text if text in LANGUAGES else None


def ui_language(state: dict | None) -> str:
    """The interface language of one task (D-176): `state.ui_language`, else English.

    A state written before plan 54 carries no `ui_language` at all, and an unusable value never
    reaches the interface: both degrade to `en`, the floor every pack falls back to (D-168).
    """
    if not isinstance(state, dict):
        return DEFAULT
    return normalize(state.get("ui_language")) or DEFAULT


def _pack_path(code: str) -> Path:
    return (PACK_DIR / f"{code}.json").resolve()


def _read_pack(code: str) -> dict:
    """The parsed pack, or raise `PackUnavailable` when it is missing or unusable."""
    key = str(_pack_path(code))
    if key in _cache:
        return _cache[key]
    try:
        document = json.loads(_pack_path(code).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise PackUnavailable(code)
    if not isinstance(document, dict):
        raise PackUnavailable(code)
    if not isinstance(document.get("memo"), dict) or not isinstance(document.get("ui"), dict):
        raise PackUnavailable(code)
    _cache[key] = document
    return document


def load(code: str) -> dict:
    """`"en"` -> `i18n_en.EN` (no file needed); else the JSON pack; raises `PackUnavailable`."""
    if code == "en":
        return i18n_en.EN
    return _read_pack(code)


def available(code: str) -> bool:
    """True when `load()` succeeds."""
    try:
        load(code)
    except PackUnavailable:
        return False
    return True


def _lookup(code: str, key: str) -> tuple[object, bool]:
    """`(value, found)` — walk the dotted key through the pack, then through EN."""
    node: object = load(code)
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            node = None
            break
        node = node[part]
    if node is not None:
        return node, True
    node = i18n_en.EN
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, node is not None


def node(code: str, key: str) -> object:
    """The raw node (list, dict, bool, str) with the same lookup rules as `t` (D-168)."""
    try:
        value, found = _lookup(code, key)
    except PackUnavailable:
        if key.startswith("ui."):
            value, found = _lookup("en", key)
        else:
            raise
    if not found:
        raise KeyError(key)
    return value


def t(code: str, key: str, **fmt) -> str:
    """The dotted key rendered with `**fmt`; missing key -> EN value (D-168).

    A `memo.*` key of an unreadable pack raises `PackUnavailable` — the memo language is
    never silently replaced. A `ui.*` key of an unreadable pack answers from EN.
    """
    try:
        value, found = _lookup(code, key)
    except PackUnavailable:
        if key.startswith("ui."):
            value, found = _lookup("en", key)
        else:
            raise
    if not found:
        raise KeyError(key)
    text = str(value)
    return text.format(**fmt) if fmt else text
