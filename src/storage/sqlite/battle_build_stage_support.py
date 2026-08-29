# 战报冻结边界的角色与弧盘等级/突破规范化。
"""Validate explicit advancement state before an immutable battle freeze."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .user_data_support import (
    UserDataError,
    UserDataValidationError,
    _integer,
    _valid_breakthrough_stage_for_level,
)


def _optional_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label, minimum=0)


def normalize_frozen_advancement(
    profile: Mapping[str, Any],
    character_id: int,
) -> tuple[int, int, str | None, int | None, int | None, dict[str, Any]]:
    raw_level = profile.get("character_level")
    raw_stage = profile.get("breakthrough_stage")
    character_level = _integer(
        80 if raw_level is None else raw_level,
        "character_level",
        minimum=1,
    )
    breakthrough_stage = _integer(
        6 if raw_stage is None else raw_stage,
        "breakthrough_stage",
        minimum=0,
    )
    if not _valid_breakthrough_stage_for_level(
        character_level, breakthrough_stage
    ):
        raise UserDataValidationError("角色等级与突破阶段不匹配")
    fork_id = str(profile.get("fork_id") or "").strip() or None
    fork_level = _optional_integer(profile.get("fork_level"), "fork_level")
    fork_stage = _optional_integer(
        profile.get("fork_breakthrough_stage"),
        "fork_breakthrough_stage",
    )
    if fork_id is None:
        fork_level = None
        fork_stage = None
    elif fork_level is None:
        raise UserDataError(f"角色 {character_id} 的冻结弧盘缺少等级")
    elif fork_stage is None:
        raise UserDataError(f"角色 {character_id} 的冻结弧盘缺少突破阶段")
    elif not _valid_breakthrough_stage_for_level(fork_level, fork_stage):
        raise UserDataValidationError("弧盘等级与突破阶段不匹配")
    frozen_profile = dict(profile)
    frozen_profile["fork_breakthrough_stage"] = fork_stage
    return (
        character_level,
        breakthrough_stage,
        fork_id,
        fork_level,
        fork_stage,
        frozen_profile,
    )


__all__ = ["normalize_frozen_advancement"]
