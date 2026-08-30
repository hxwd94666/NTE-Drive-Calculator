# 组合静态怪物、轨外之境、争锋赏宴和魔女赐福目录。
"""Qt-free target/environment catalog for battle analysis."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any


_MONSTER_KEY = re.compile(r"(?i)(boss|mon)_0*(\d+)")


def _monster_key(value: object) -> str:
    match = _MONSTER_KEY.search(str(value or ""))
    return "" if match is None else f"{match.group(1).lower()}_{int(match.group(2))}"


def _profile(row: dict[str, Any]) -> dict[str, Any]:
    resistances = {
        key: (
            float(value.get("resistance_base") or 0.0)
            if isinstance(value, dict)
            else float(value or 0.0)
        )
        for key, value in (row.get("resistances") or {}).items()
    }
    return {
        "health_base": row.get("health_base"),
        "health_up": row.get("health_up"),
        "health_add": row.get("health_add"),
        "defense_base": row.get("defense_base"),
        "defense_up": row.get("defense_up"),
        "defense_add": row.get("defense_add"),
        "topple_limit": row.get("topple_limit"),
        "resistances": resistances,
    }


class BattleTargetCatalogService:
    """Build selection-ready dictionaries from static DAO rows."""

    @classmethod
    def load(cls, static_dao) -> dict[str, Any]:
        names = {
            _monster_key(row["monster_manual_id"]): row
            for row in static_dao.list_monster_display_names()
            if _monster_key(row["monster_manual_id"])
        }
        open_world = []
        open_world_groups = {
            "Normal": {
                "category_id": "open_world:normal",
                "name_zh": "普通敌人",
                "targets": [],
            },
            "Elite": {
                "category_id": "open_world:elite",
                "name_zh": "精英敌人",
                "targets": [],
            },
            "WeeklyBoss": {
                "category_id": "open_world:world_boss",
                "name_zh": "异象追猎",
                "targets": [],
            },
        }
        for row in static_dao.list_open_world_target_catalog():
            variants = list(row.get("variants") or ())
            variant = next(
                (item for item in variants if item.get("profile")),
                variants[0] if variants else {},
            )
            profile = dict(variant.get("profile") or {})
            target = {
                "target_id": str(row["target_id"]),
                "name_zh": str(row.get("name_zh") or row["target_id"]),
                "subtitle": " · ".join(
                    str(value)
                    for value in (row.get("place_zh"), row.get("enemy_type"))
                    if value
                ),
                "monster_level": float(variant.get("monster_level") or 1.0),
                "profile_set": variant.get("profile_set"),
                "pack_id": variant.get("pack_id"),
                "profile": _profile(profile),
                "variants": variants,
            }
            group = open_world_groups.get(str(row.get("enemy_type") or ""))
            if group is not None:
                group["targets"].append(target)
                open_world.append(target)

        grouped: dict[tuple[str, int, str], list[dict[str, Any]]] = defaultdict(list)
        level_names: dict[tuple[str, int], str] = {}
        for row in static_dao.list_outer_realm_target_presets():
            config_id = str(row["level_config_id"])
            level_id = int(row["level_id"])
            stage = str(row["fight_stage"])
            level_names[(config_id, level_id)] = str(row.get("name_zh") or "")
            monster = names.get(_monster_key(row.get("monster_class_path")), {})
            grouped[(config_id, level_id, stage)].append({
                "target_id": (
                    f"{config_id}:{level_id}:{stage}:"
                    f"{row['spawn_ordinal']}:{row['monster_ordinal']}"
                ),
                "name_zh": str(monster.get("name_zh") or row["monster_class_path"]),
                "monster_class_path": str(row["monster_class_path"]),
                "monster_level": float(row.get("monster_level") or 1.0),
                "monster_count": int(row.get("monster_count") or 1),
                "wave": int(row.get("wave") or 1),
                "profile_set": row.get("profile_set"),
                "pack_id": row.get("pack_id"),
                "profile": _profile(row),
            })
        configs = []
        for config in static_dao.list_outer_realm_configs()[:2]:
            config_id = str(config["level_config_id"])
            levels = []
            for level_id in range(1, int(config["max_level"]) + 1):
                halves = []
                for stage in (
                    "EAbyssFightStage::FirstHalf",
                    "EAbyssFightStage::SecondHalf",
                ):
                    targets = grouped.get((config_id, level_id, stage), [])
                    if targets:
                        halves.append({
                            "stage": stage,
                            "name_zh": "上半" if "First" in stage else "下半",
                            "targets": targets,
                        })
                if halves:
                    levels.append({
                        "level_id": level_id,
                        "name_zh": level_names.get((config_id, level_id), ""),
                        "halves": halves,
                    })
            configs.append({
                "level_config_id": config_id,
                "max_level": int(config["max_level"]),
                "levels": levels,
                "season_buff": config.get("season_buff"),
            })
        clone_categories = []
        visible_clone_names = {
            "经验及甲硬币",
            "异能升级材料",
            "弧盘突破材料",
            "空幕",
            "异象巡礼",
        }
        for category in static_dao.list_clone_activity_catalog():
            if category.get("name_zh") not in visible_clone_names:
                continue
            for activity in category.get("activities") or ():
                for difficulty in activity.get("difficulties") or ():
                    for member in difficulty.get("spawn_members") or ():
                        member["name_zh"] = str(
                            member.get("monster_name_zh")
                            or member.get("monster_template_name")
                            or "未命名目标"
                        )
            clone_categories.append(category)
        return {
            "open_world": open_world,
            "open_world_categories": list(open_world_groups.values()),
            "clone_categories": clone_categories,
            "outer_realm": configs,
            "feast": static_dao.list_feast_stages(),
            "witch_buffs": static_dao.list_divination_buffs(),
        }
