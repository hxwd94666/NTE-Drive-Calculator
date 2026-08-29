# 战报边际候选的养成摘要，显式展示上限等级的突破歧义。
"""Qt-free battle build summary labels."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.advancement_stage_service import (
    character_growth_choices,
    fork_breakthrough_choices,
)


def character_level_summary(
    detail: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> str:
    level = int(profile.get("character_level") or 1)
    stage = profile.get("breakthrough_stage")
    choices = character_growth_choices(detail.get("growth_rows") or (), level)
    selected = next(
        (
            row
            for row in choices
            if stage is not None
            and int(row.get("breakthrough_stage") or 0) == int(stage)
        ),
        None,
    )
    suffix = {
        "breakthrough_before": "（突破前）",
        "breakthrough_after": "（突破后）",
    }.get(str((selected or {}).get("state") or ""), "")
    return f"Lv.{level}{suffix}"


def fork_level_summary(
    detail: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> str:
    fork_id = str(profile.get("fork_id") or "")
    if not fork_id:
        return "未装备弧盘"
    level = int(profile.get("fork_level") or 1)
    refinement = int(profile.get("fork_refinement_level") or 1)
    template = next(
        (
            row
            for row in detail.get("forks") or ()
            if str(row.get("fork_id") or "") == fork_id
        ),
        None,
    )
    choices = fork_breakthrough_choices(
        (template or {}).get("breakthroughs") or (),
        level,
    )
    suffix = ""
    if len(choices) > 1:
        stage = profile.get("fork_breakthrough_stage")
        if stage is None:
            suffix = "（突破未知）"
        elif int(stage) == int(choices[0].get("stage") or 0):
            suffix = "（突破前）"
        elif int(stage) == int(choices[-1].get("stage") or 0):
            suffix = "（突破后）"
    return f"弧盘 Lv.{level}{suffix}/精{refinement}"


__all__ = ["character_level_summary", "fork_level_summary"]
