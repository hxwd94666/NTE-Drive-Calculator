# 加载 locales 下的界面文案目录与游戏术语显示名映射。
"""Translation catalogue and game-term glossary loading.

Source strings are written in Chinese and used directly as catalogue keys, so an
untranslated string degrades to the original Chinese instead of to a key name.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from src.integrations.bundled_resources import bundled_locales_dir


SOURCE_LANGUAGE = "zh_CN"
DEFAULT_LANGUAGE = SOURCE_LANGUAGE
LANGUAGES: tuple[str, ...] = (SOURCE_LANGUAGE, "en")
LANGUAGE_LABELS = {SOURCE_LANGUAGE: "简体中文", "en": "English"}

# Glossary sections are flattened in this order; earlier sections win a collision.
# English needs a singular form where Chinese does not. A sibling key names the
# field that decides it: "<source>::one::<field>".
PLURAL_ONE_MARKER = "::one::"

GLOSSARY_SECTIONS = (
    "stats",
    "elements",
    "reactions",
    "qualities",
    "fork_types",
    "suits",
    "characters",
    "forks",
    "ui_terms",
)


def normalized_language(value: object, default: str = DEFAULT_LANGUAGE) -> str:
    language = str(value or "").strip()
    return language if language in LANGUAGES else default


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


@lru_cache(maxsize=len(LANGUAGES))
def load_catalog(language: str) -> dict[str, str]:
    """Return the UI string catalogue for ``language``.

    The source language needs no catalogue: its strings are the keys.
    """
    if language == SOURCE_LANGUAGE:
        return {}
    payload = _read_json(bundled_locales_dir() / f"{language}.json")
    # An empty translation is kept deliberately: unit suffixes and measure words
    # such as "份" have no English equivalent and must render as nothing.
    return {
        str(key): str(value)
        for key, value in payload.items()
        if not str(key).startswith("_")
        and PLURAL_ONE_MARKER not in str(key)
        and isinstance(value, str)
    }


@lru_cache(maxsize=len(LANGUAGES))
def load_plurals(language: str) -> dict[str, tuple[str, str]]:
    """Return ``{source: (field, singular text)}`` for ``language``.

    Split out at load time so ``tr`` stays a dictionary lookup instead of
    scanning every catalogue key on each call.
    """
    if language == SOURCE_LANGUAGE:
        return {}
    payload = _read_json(bundled_locales_dir() / f"{language}.json")
    plurals: dict[str, tuple[str, str]] = {}
    for key, value in payload.items():
        key = str(key)
        if key.startswith("_") or PLURAL_ONE_MARKER not in key or not isinstance(value, str):
            continue
        source, _, field = key.partition(PLURAL_ONE_MARKER)
        if source and field and value:
            plurals[source] = (field, value)
    return plurals


@lru_cache(maxsize=len(LANGUAGES))
def load_glossary(language: str) -> dict[str, str]:
    """Return game-term display names for ``language``.

    Game terms stay Chinese as keys because they are matched against OCR output
    and used as static-database lookups; only the display name changes.
    """
    if language == SOURCE_LANGUAGE:
        return {}
    payload = _read_json(bundled_locales_dir() / f"glossary.{language}.json")
    flattened: dict[str, str] = {}
    for section in GLOSSARY_SECTIONS:
        entries = payload.get(section)
        if not isinstance(entries, dict):
            continue
        for key, value in entries.items():
            if isinstance(value, str) and value:
                flattened.setdefault(str(key), value)
    return flattened


@lru_cache(maxsize=len(LANGUAGES))
def load_game_text(language: str) -> dict[str, str]:
    """Return long-form game text keyed as ``Namespace::Key``.

    Built by ``tools/game_data/build_game_text_locale.py`` from a locres export
    joined on the string-table keys the static database already stores.
    """
    if language == SOURCE_LANGUAGE:
        return {}
    payload = _read_json(bundled_locales_dir() / f"gametext.{language}.json")
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        str(key): str(value)
        for key, value in entries.items()
        if isinstance(value, str) and value
    }


def clear_cache() -> None:
    """Drop cached catalogues so the next lookup re-reads from disk."""
    load_catalog.cache_clear()
    load_plurals.cache_clear()
    load_glossary.cache_clear()
    load_game_text.cache_clear()
