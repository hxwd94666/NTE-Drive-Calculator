# 界面本地化公共入口：语言状态、文案翻译与游戏术语显示名。
"""Public entry point for UI localisation.

``tr`` translates interface copy; ``display_term`` maps a game term to its
display name while leaving the term itself — an OCR and database key — intact.
"""

from __future__ import annotations

from src.i18n.catalog import (
    DEFAULT_LANGUAGE,
    LANGUAGE_LABELS,
    LANGUAGES,
    SOURCE_LANGUAGE,
    clear_cache,
    normalized_language,
)
from src.i18n.runtime import (
    available_languages,
    current_language,
    display_localized,
    display_term,
    display_terms,
    display_text,
    is_source_language,
    set_language,
    tr,
)

__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "LANGUAGE_LABELS",
    "SOURCE_LANGUAGE",
    "available_languages",
    "clear_cache",
    "current_language",
    "display_localized",
    "display_term",
    "display_terms",
    "display_text",
    "is_source_language",
    "normalized_language",
    "set_language",
    "tr",
]
