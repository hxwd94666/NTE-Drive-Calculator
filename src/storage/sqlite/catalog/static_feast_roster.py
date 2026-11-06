# 按争锋副本的正式刷怪清单与同级属性包读取全部阶段目标。
from __future__ import annotations

from typing import Any


def feast_difficulty_targets(dao: Any, stage: dict, difficulty: dict) -> list[dict]:
    """保留已闭合的独立身份，并明确标记未闭合成员；不替额外目标猜等级或属性。"""
    # 明确的逐成员等级优先；未观测到时只匹配同级包，不拿另一档包猜补。
    columns = {row["name"] for row in dao._rows("PRAGMA table_info(clone_spawn_member)", ())}
    level = "CASE WHEN s.configured_monster_level > 0 THEN s.configured_monster_level ELSE ? END" if "configured_monster_level" in columns else "?"
    members = dao._rows(
        f"""SELECT s.monster_template_name, s.monster_template_path, s.monster_count,
                  v.profile_set, v.pack_id, v.threshold_level
           FROM clone_activity_difficulty AS d
           JOIN clone_spawn_member AS s USING (spawn_id)
           LEFT JOIN monster_instance_profile_variant AS v
             ON v.static_table = 'monster_static_big_world'
            AND v.monster_id = s.monster_template_name COLLATE NOCASE
            AND v.variant_kind = 'clone_level' AND v.threshold_level = {level}
           WHERE d.clone_id = ? AND d.difficulty_level = ?
           ORDER BY s.wave_ordinal, s.entry_ordinal""",
        (difficulty["monster_level"], stage["stage_id"], difficulty["difficulty_id"]),
    )
    targets, unresolved = [], []
    for member in members:
        if not member["profile_set"] or not member["pack_id"]:
            unresolved.append(member['monster_template_name'])
            continue
        profile = dao.get_enemy_combat_profile(member["profile_set"], member["pack_id"])
        if not profile or not profile.get("health_base"):
            unresolved.append(member['monster_template_name'])
            continue
        targets.append({
            "target_id": member["monster_template_name"],
            "monster_class_path": member["monster_template_path"],
            "monster_count": member["monster_count"],
            "name_zh": difficulty["boss_name_zh"],
            "monster_level": member["threshold_level"],
            "profile_set": member["profile_set"], "pack_id": member["pack_id"],
            "profile": profile,
        })
    difficulty['unresolved_target_ids'] = unresolved
    difficulty['roster_complete'] = bool(members) and not unresolved
    # 特殊副本可能同时登记不同等级的附加对象；正式主目标仍可使用，观测到未知对象时严格匹配会失败。
    primary = str(stage.get('boss_monster_id') or '').casefold()
    if primary and not any(row['target_id'].casefold() == primary for row in targets):
        return []
    targets.sort(key=lambda row: row['target_id'].casefold() != primary)
    return targets
