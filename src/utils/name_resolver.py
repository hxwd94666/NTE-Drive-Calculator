"""Fuzzy name matching helpers for sets, stats, and user input."""

import difflib
import re
import unicodedata
from collections.abc import Iterable


def normalize_name(value: str | None) -> str:
    """Normalize user/OCR names for lookup without changing stored display names."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = re.sub(r"\s+", "", text)
    return text.casefold()


def resolve_name(value: str | None, choices: Iterable[str], cutoff: float = 0.82) -> str | None:
    """Return the canonical choice matching value, with exact normalized match first."""
    names = [name for name in choices if name]
    if not value or not names:
        return None

    index: dict[str, str] = {}
    for name in names:
        key = normalize_name(name)
        if key and key not in index:
            index[key] = name

    target = normalize_name(value)
    if target in index:
        return index[target]

    matches = difflib.get_close_matches(target, list(index.keys()), n=1, cutoff=cutoff)
    if matches:
        return index[matches[0]]
    return None


def canonical_name(value: str | None, choices: Iterable[str], cutoff: float = 0.82) -> str:
    return resolve_name(value, choices, cutoff=cutoff) or (value or "")
