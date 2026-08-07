# 定义评分和分配共享的官方套装标识工具。
"""Official suit identity helpers shared by scoring and allocation."""

from __future__ import annotations

from typing import Mapping

from src.utils.set_name import normalize_set_display_name


def normalized_suit_name(value: object) -> str:
    """Return a comparison-only display-name key.

    Names remain presentation data.  This key exists only for legacy inputs
    that predate official ``suit_id`` propagation.
    """

    return normalize_set_display_name(value).casefold()


def suit_id_for_target(
    target: object,
    sets_db: Mapping[str, Mapping],
) -> str | None:
    """Resolve a legacy set target to its official suit ID when possible."""

    text = str(target or "").strip()
    if not text:
        return None

    for set_name, row in sets_db.items():
        suit_id = str(row.get("suit_id") or "").strip()
        if suit_id and text == suit_id:
            return suit_id
        if text == str(set_name):
            return suit_id or str(set_name)

    target_name = normalized_suit_name(text)
    for set_name, row in sets_db.items():
        if normalized_suit_name(set_name) == target_name:
            return str(row.get("suit_id") or set_name).strip() or None
    return None


def tape_suit_key(tape: object) -> str:
    """Return the stable candidate-bucket key for one tape."""

    suit_id = str(getattr(tape, "suit_id", None) or "").strip()
    if suit_id:
        return f"id:{suit_id}"
    return f"name:{normalized_suit_name(getattr(tape, 'set_name', ''))}"


def tape_matches_suit_target(
    tape: object,
    target: object,
    sets_db: Mapping[str, Mapping],
) -> bool:
    """Match by official ID, with a name-only fallback for legacy objects."""

    if target is None:
        return True

    tape_suit_id = str(getattr(tape, "suit_id", None) or "").strip()
    target_suit_id = suit_id_for_target(target, sets_db)
    if tape_suit_id:
        return target_suit_id is not None and tape_suit_id == target_suit_id

    tape_name = normalized_suit_name(getattr(tape, "set_name", ""))
    if tape_name == normalized_suit_name(target):
        return True
    if target_suit_id is None:
        return False
    return any(
        tape_name == normalized_suit_name(set_name)
        for set_name, row in sets_db.items()
        if str(row.get("suit_id") or set_name).strip() == target_suit_id
    )
