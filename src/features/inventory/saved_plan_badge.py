# 生成已保存配装方案的互斥来源标签。
"""Presentation metadata for persisted loadout plans."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def display_strategy_mode(payload: Mapping[str, Any]) -> str:
    """Return the one mutually exclusive origin/strategy badge for a plan."""

    return "game_inventory" if payload.get("source") == "game_inventory" else str(payload.get("strategy") or "")
