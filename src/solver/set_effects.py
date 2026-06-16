# 生成不同套装效果模式下需要强制匹配的套装形状。
"""Set-effect mode helpers for blueprint generation."""

from __future__ import annotations

from itertools import combinations


FOUR_PIECE = "four_piece"
TWO_PIECE = "two_piece"
NO_EFFECT = "none"
SET_EFFECT_MODES = (FOUR_PIECE, TWO_PIECE, NO_EFFECT)


def normalize_set_effect_mode(mode: str | None) -> str:
    if mode in SET_EFFECT_MODES:
        return str(mode)
    return FOUR_PIECE


def set_piece_options_for_mode(set_shapes: list[str], mode: str | None) -> list[list[str]]:
    normalized = normalize_set_effect_mode(mode)
    shapes = list(set_shapes or [])

    if normalized == NO_EFFECT:
        return [[]]
    if normalized == TWO_PIECE:
        if len(shapes) <= 2:
            return [shapes]
        return [list(combo) for combo in combinations(shapes, 2)]
    return [shapes]
