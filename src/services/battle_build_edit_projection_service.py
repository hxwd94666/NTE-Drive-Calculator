# 把持久修改副本或边际内存候选投影到战报计算快照。
"""Apply one explicit build-edit projection without owning persistence."""

from __future__ import annotations

from typing import Any, Mapping

from src.services.battle_build_equipment_service import apply_equipment_override


def apply_battle_build_edit(
    build: dict[str, Any],
    build_edit: Mapping[str, Any] | None,
) -> None:
    build["has_user_edit"] = build_edit is not None
    build["user_edit_active"] = bool((build_edit or {}).get("is_active"))
    if not build["user_edit_active"]:
        return
    edited_by_character = {
        int(row["character_id"]): row
        for row in (build_edit or {}).get("characters") or ()
    }
    for character in build.get("characters") or ():
        edited = edited_by_character.get(int(character["character_id"]))
        if edited is None:
            continue
        profile = dict(edited.get("profile") or {})
        equipment_overridden = apply_equipment_override(character, profile)
        frozen_world_bonus = [
            dict(row)
            for row in character.get("stats") or ()
            if str(row.get("source_group") or "") == "world_bonus"
        ]
        character.update({
            "profile_source": "user_edited_snapshot",
            "character_level": int(edited["character_level"]),
            "breakthrough_stage": int(edited["breakthrough_stage"]),
            "awakening_level": int(edited["awakening_level"]),
            "fork_id": edited.get("fork_id"),
            "fork_level": edited.get("fork_level"),
            "fork_breakthrough_stage": edited.get(
                "fork_breakthrough_stage"
            ),
            "fork_refinement_level": edited.get("fork_refinement_level"),
            "selected_skill_id": edited.get("selected_skill_id"),
            "profile": profile,
            "skills": list(edited.get("skills") or ()),
            # 世界加成属于本场冻结环境，不随养成/配装反事实改用当前账号值。
            "stats": frozen_world_bonus,
            "stat_snapshot_source": "missing",
            "_edited_snapshot_active": True,
            "_edited_equipment_active": equipment_overridden,
        })


__all__ = ["apply_battle_build_edit"]
