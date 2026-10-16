# 只冻结官方直伤原值和正式身份；单位归一化、成长/弧盘汇总与面板公式归 Rust。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy


def freeze_direct_projection_inputs(request, details, source_inventory, *, geometry_cells=None, checkpoint=None):
    """只按已冻结身份挑选原始行，不求值、不补写主词条或合并属性。"""
    sources = {}
    for index, item in enumerate(source_inventory):
        if checkpoint is not None and index % 64 == 0:
            checkpoint()
        uid = item.get("uid")
        if uid in sources:
            raise ValueError("本次冻结库存存在重复装备身份")
        sources[uid] = item.get("official")
    inventory = {}
    for index, item in enumerate(request.inventory):
        if checkpoint is not None and index % 64 == 0:
            checkpoint()
        raw = sources.get(item.uid)
        if not isinstance(raw, Mapping):
            raise ValueError("本次冻结库存缺少正式属性证据")
        inventory[item.uid] = deepcopy({key: raw[key] for key in (
            "uid", "uid_slot", "uid_serial", "kind", "item_id", "quality", "grid", "grid_count", "geometry",
            "main_stats", "sub_stats",
        ) if key in raw})
    result = {}
    for role, detail in details:
        if checkpoint is not None:
            checkpoint()
        if role not in request.role_order or role in result:
            raise ValueError("直伤原值的角色范围与冻结分配不同")
        profile = detail.get("profile") or {}
        character = detail.get("character") or {}
        growth = [row for row in detail.get("growth_rows") or ()
                  if row.get("level") == profile.get("character_level")
                  and row.get("breakthrough_stage") == profile.get("breakthrough_stage")]
        forks = []
        for fork in detail.get("forks") or ():
            if fork.get("fork_id") != profile.get("fork_id"):
                continue
            forks.append({key: deepcopy(fork.get(key)) for key in (
                "fork_id", "permanent_review_status", "breakthroughs", "permanent_properties",
            )})
            forks[-1]["upgrade_levels"] = deepcopy([row for row in fork.get("upgrade_levels") or ()
                                                    if row.get("level") == profile.get("fork_level")])
        result[role] = deepcopy({
            "character": {key: character.get(key) for key in ("character_id", "element_type")},
            "profile": {key: profile.get(key) for key in (
                "character_level", "breakthrough_stage", "fork_id", "fork_level",
                "fork_breakthrough_stage", "fork_refinement_level", "likeability_level_10_enabled",
            )},
            "growth_rows": growth, "forks": forks,
            "shape_bonus": detail.get("shape_bonus") or {},
            "attributes": {key: {"show_percent": value.get("show_percent")} for key, value in
                           (detail.get("attributes") or {}).items()},
            "likeability_bonus": detail.get("likeability_bonus") or {},
            "world_bonus": detail.get("world_bonus") or {},
        })
    if checkpoint is not None:
        checkpoint()
    return {"details": result, "inventory": inventory, "geometry_cells": deepcopy(dict(geometry_cells or {}))}
