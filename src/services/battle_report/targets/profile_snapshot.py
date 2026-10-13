# 把目标选择器的静态目录行冻结成可持久化的逐目标属性档案。
"""Pure projection from selection catalog rows to target profile snapshots."""

from __future__ import annotations

from typing import Any


def battle_target_profile_snapshots(target: dict[str, Any]) -> list[dict[str, Any]]:
    variants = tuple(target.get("variants") or ())
    sources = variants if variants else (target,)
    result = []
    for source in sources:
        profile = dict(source.get("profile") or target.get("profile") or {})
        health_base = profile.get("health_base")
        if not isinstance(health_base, (int, float)) or health_base <= 0:
            continue
        resistances = {
            key: (
                float(value.get("resistance_base") or 0.0)
                if isinstance(value, dict)
                else float(value or 0.0)
            )
            for key, value in (profile.get("resistances") or {}).items()
        }
        selection_id = str(target.get("target_id") or "")
        monster_class_path = str(
            source.get("monster_template_name")
            or target.get("monster_class_path")
            or ""
        )
        static_target_id = str(
            source.get("monster_id") or monster_class_path or selection_id
        )
        result.append({
            "static_target_id": static_target_id,
            "selection_target_id": selection_id,
            "target_name": str(target.get("name_zh") or static_target_id),
            "monster_class_path": monster_class_path,
            "monster_count": max(1, int(target.get("monster_count") or 1)),
            "max_hp": (
                float(health_base)
                * (1.0 + float(profile.get("health_up") or 0.0))
                + float(profile.get("health_add") or 0.0)
            ),
            "monster_level": float(
                source.get("monster_level")
                or target.get("monster_level")
                or 1.0
            ),
            "defense_base": profile.get("defense_base"),
            "defense_up": float(profile.get("defense_up") or 0.0),
            "defense_add": float(profile.get("defense_add") or 0.0),
            "topple_limit": float(profile.get("topple_limit") or 50.0),
            "resistances": resistances,
            "profile_set": str(
                source.get("profile_set") or target.get("profile_set") or ""
            ),
            "pack_id": str(source.get("pack_id") or target.get("pack_id") or ""),
        })
    return result
