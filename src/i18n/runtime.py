# 保存当前界面语言，并提供文案与游戏术语的查表函数。
"""Active-language state and the lookup functions used across the UI."""

from __future__ import annotations

from collections.abc import Mapping

from src.i18n.catalog import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    SOURCE_LANGUAGE,
    load_catalog,
    load_game_text,
    load_glossary,
    load_plurals,
    normalized_language,
)


_active_language = DEFAULT_LANGUAGE
_active_catalog: dict[str, str] = {}
_active_glossary: dict[str, str] = {}
_active_game_text: dict[str, str] = {}
_active_plurals: dict[str, tuple[str, str]] = {}


def current_language() -> str:
    return _active_language


def available_languages() -> tuple[str, ...]:
    return LANGUAGES


def set_language(language: object) -> str:
    """Activate ``language`` and return the value actually applied."""
    global _active_language, _active_catalog, _active_glossary, _active_game_text
    global _active_plurals
    resolved = normalized_language(language)
    _active_language = resolved
    _active_catalog = load_catalog(resolved)
    _active_glossary = load_glossary(resolved)
    _active_game_text = load_game_text(resolved)
    _active_plurals = load_plurals(resolved)
    return resolved


def tr(text: str, /, **fields: object) -> str:
    """Translate a Chinese source string, falling back to the source itself.

    ``fields`` are applied with :meth:`str.format` so a translation may reorder
    placeholders. Formatting failures fall back to the unformatted string rather
    than raising into the UI.
    """
    if not text:
        return text
    # Some UI copy is also a game term (卡带, 副词条). Fall back to the glossary
    # so the display name wins over the untranslated source.
    translated = _active_catalog.get(text) or _active_glossary.get(text) or text
    if not fields:
        return translated
    # A singular variant wins only when its own named field is 1, so another
    # integer in the same string (a snapshot or job number) cannot trigger it.
    singular = _active_plurals.get(text)
    if singular is not None and fields.get(singular[0]) == 1:
        translated = singular[1]
    try:
        return translated.format(**fields)
    except (KeyError, IndexError, ValueError):
        return translated


def display_term(term: str) -> str:
    """Return the display name for a game term, keeping the key unchanged."""
    if not term:
        return term
    hit = _active_glossary.get(term)
    if hit:
        return hit
    # Several views strip the game's decorative 「」 before display, so retry
    # with them restored rather than teaching every caller about brackets.
    return _active_glossary.get(f"「{term}」", term)


def display_terms(terms) -> list[str]:
    return [display_term(term) for term in terms]


# nte-core ships every name in en/ja/zh_cn, so a localized name usually comes
# straight from the game data and needs no glossary entry.
_PAYLOAD_KEYS = {
    "zh_CN": ("zh_cn", "zh-CN", "zh", "cn"),
    "en": ("en", "en_US", "en-US"),
}


def display_localized(names: object, source_text: object = "") -> str:
    """Return the game data's own name for the active language.

    Falls back to the glossary display name for ``source_text`` when the payload
    carries nothing for this language, so vision-sourced rows still translate.
    """
    if isinstance(names, Mapping):
        for key in _PAYLOAD_KEYS.get(_active_language, ()):
            text = names.get(key)
            if text:
                return str(text)
    return display_term(source_text) if source_text else ""


def display_text(text_table: object, text_key: object, fallback: str = "") -> str:
    """Return long-form game text for a string-table key.

    ``text_table`` is the path the static database stores, e.g.
    ``/Game/Text/ST_Fork.ST_Fork``; only its last component is the namespace.
    Falls back to the Chinese the database already holds.
    """
    key = str(text_key or "")
    if not key:
        return fallback
    namespace = str(text_table or "").rsplit(".", 1)[-1]
    return _active_game_text.get(f"{namespace}::{key}".lower(), fallback)


def is_source_language() -> bool:
    return _active_language == SOURCE_LANGUAGE
