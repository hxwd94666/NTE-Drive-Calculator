# 只消费已经物化的角色/装备纯值组装详情，runtime与构建共用同一口径。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .attributes import _theory_properties


def assemble_official_role_detail(inputs: Mapping[str, Any]) -> dict[str, Any]:
    """不读取文件/DAO、不创建发行身份，不复制面板或评分公式。"""
    character_id = int(inputs["character_id"])
    character = inputs["character"]
    catalog_scope = inputs["catalog_scope"]
    resolved_static_database_path = inputs["resolved_static_database_path"]
    resolved_static_schema_version = inputs["resolved_static_schema_version"]
    profile = inputs["profile"]
    growth_rows = inputs["growth_rows"]
    skills = inputs["skills"]
    awakenings = inputs["awakenings"]
    likeability_bonus = inputs["likeability_bonus"]
    world_bonus = inputs["world_bonus"]
    forks = inputs["forks"]
    equipment_plan = inputs["equipment_plan"]
    shape_bonus = inputs["shape_bonus"]
    graduation_template = inputs["graduation_template"]
    graduation_extra_shape_count = inputs["graduation_extra_shape_count"]
    attributes = inputs["attributes"]
    item_names = inputs["item_names"]
    item_icon_paths = inputs["item_icon_paths"]
    weights = inputs["weights"]
    weight_record = inputs["weight_record"]
    replacement_items = inputs["replacement_items"]
    current_items = inputs["current_items"]
    current_calculation_items = inputs["current_calculation_items"]
    saved_items = inputs["saved_items"]
    saved_calculation_items = inputs["saved_calculation_items"]
    saved_plan = inputs["saved_plan"]
    selected_slot = inputs["selected_slot"]
    selected_slot_name = inputs["selected_slot_name"]
    extra_saved_contexts = inputs["extra_saved_contexts"]
    include_replacement_candidates = inputs["include_replacement_candidates"]
    theory_ids = _theory_properties(weights)
    has_saved_weights = any(float(value) > 0 for value in weights.values())
    theory_weights = {
        property_id: float(weights.get(property_id, 0.0)) if has_saved_weights else 1.0
        for property_id in theory_ids
    }
    main_ids = tuple((equipment_plan or {}).get("core_attribute_ids") or ())
    return {
        "character": character,
        "catalog_scope": catalog_scope,
        "static_database_path": resolved_static_database_path,
        "static_schema_version": resolved_static_schema_version,
        "icon_path": inputs["icon_path"],
        "profile": profile,
        "growth_rows": growth_rows,
        "skills": skills,
        "awakenings": awakenings,
        "likeability_bonus": likeability_bonus,
        "world_bonus": world_bonus.to_payload(),
        "forks": forks,
        "equipment_plan": equipment_plan,
        "shape_bonus": shape_bonus,
        "graduation_template": graduation_template,
        "graduation_extra_shape_count": graduation_extra_shape_count,
        "attributes": attributes,
        "item_names": item_names,
        "item_icon_paths": item_icon_paths,
        "property_weights": weights,
        "main_property_weights": {
            str(key): float(value)
            for key, value in (weight_record.get("main_property_weights") or {}).items()
        },
        "property_weight_source": str(
            weight_record.get("source_kind") or "default"
        ),
        "property_weights_from_account": str(weight_record.get("source_kind") or "") in ("account", "custom"),
        "weight_source_character_id": weight_record.get("weight_source_character_id", character_id),
        "theory_weights": theory_weights,
        "theory_weights_persisted": "seeded_at_utc" in weight_record,
        "replacement_items": replacement_items,
        "replacement_candidates_loaded": include_replacement_candidates,
        "equipment_contexts": {
            "current": {
                "title": "游戏当前",
                "items": current_items,
                "calculation_items": current_calculation_items,
                "available": bool(current_items),
            },
            "saved": {
                "title": f"已保存配装 · {selected_slot_name}",
                "items": saved_items,
                "calculation_items": saved_calculation_items,
                "plan": saved_plan,
                "slot_id": (int(selected_slot["slot"]["slot_id"]) if selected_slot is not None else None),
                "slot_name": selected_slot_name,
                "available": bool(saved_items),
            },
            **extra_saved_contexts,
            "theory": {
                "title": "理论最优",
                "items": (),
                "available": equipment_plan is not None,
                "core_item_id": (equipment_plan or {}).get("core_item_id"),
                "core_main_property_ids": main_ids,
                "property_ids": theory_ids,
                "numeric_ready": False,
            },
        },
    }
