# 提供图纸按具体形状数量去重的通用工具。
from __future__ import annotations

from collections import Counter
from typing import Iterable


def blueprint_piece_signature(blueprint: dict) -> tuple[tuple[str, int], ...]:
    """Return a stable identity for the exact shape/count combination."""
    pieces = list(blueprint.get("set_pieces") or []) + list(blueprint.get("extra_pieces") or [])
    counts = Counter(str(piece) for piece in pieces)
    return tuple(sorted(counts.items()))


def dedupe_blueprints_by_piece_signature(blueprints: Iterable[dict]) -> list[dict]:
    seen = set()
    unique = []
    for blueprint in blueprints:
        key = blueprint_piece_signature(blueprint)
        if key in seen:
            continue
        seen.add(key)
        unique.append(blueprint)
    return unique
