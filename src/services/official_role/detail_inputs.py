# 从runtime只读单元或构建borrowed reader物化官方角色事实，不拥有静态连接收尾。
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from src.domain.official_role.models import WORLD_BONUS_SETTING_KEY, WorldBonusSettings
from src.domain.official_role.graduation_shape import graduation_extra_shape_drive_count
from src.integrations.official_role.read_inputs import _asset_root
from src.services.characters.character_shape_bonus_service import get_effective_character_shape_bonus
from src.services.characters.character_weight_service import resolve_character_base_weights
from src.services.equipment.equipment_level_projection_service import project_equipment_items_to_max_level
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.services.official_role.skill_names import SkillNameRenderingService
from src.storage.sqlite.catalog.static_game_data_queries import StaticGameDataQueriesMixin
from src.storage.sqlite.user_data_dao import UserDataDao
from .inventory_contexts import load_role_inventory_contexts
from .profile_projection import _compatible_forks, resolve_official_role_profile


def materialize_official_role_detail_inputs(
    static_dao: StaticGameDataQueriesMixin, character_id: int, *,
    user_dao: UserDataDao | None = None, asset_root: str | Path | None = None,
    include_inventory_contexts: bool = True, include_replacement_candidates: bool = True,
    shared_database_path: str | Path | None = None,
    request_cache: dict[object, Any] | None = None, cache_identity: object | None = None,
) -> dict[str, Any]:
    """所有SQL值在调用方事务中物化；构建事实不进入runtime请求缓存。"""
    if include_inventory_contexts and user_dao is None:
        raise ValueError("库存详情必须提供账号DAO")
    if request_cache is not None and cache_identity is None:
        raise ValueError("runtime请求缓存必须提供已验证字节身份")
    static_database_path = static_dao.database_path
    def cached(key: object, factory: Callable[[], Any]) -> Any:
        # 子资源只在已验证字节身份下命中，旧路径缓存不会重贴新发行标签。
        key = (cache_identity, key)
        if request_cache is None:
            return factory()
        if key not in request_cache:
            request_cache[key] = factory()
        return request_cache[key]

    resolved_asset_root = _asset_root(asset_root)
    catalog = cached(
        ("asset_catalog", str(resolved_asset_root)),
        lambda: GameUiAssetCatalog(resolved_asset_root),
    )
    # 来源来自本次实际打开并校验的 DAO，只运输到 detail 顶层，不进入持久数据。
    resolved_static_database_path = str(static_dao.database_path)
    resolved_static_schema_version = static_dao.schema_version
    character = static_dao.get_character(character_id)
    catalog_scope = static_dao.get_catalog_scope()
    if character is None:
        raise ValueError(f"官方角色不存在：{character_id}")
    growth_rows = static_dao.list_character_panel_growth(character_id)
    skills = static_dao.list_character_skills(character_id)
    skill_name_renderer = cached(
        ("skill_name_renderer", str(static_database_path or "default")),
        lambda: SkillNameRenderingService.from_static_dao(static_dao),
    )
    for skill in skills:
        skill_id = str(skill.get("skill_id") or "")
        skill["display_name_zh"] = skill_name_renderer.render_ability_name(
            skill_id,
            fallback=skill_id or "技能",
        )
        for damage in skill.get("damage_entries") or ():
            damage_id = str(damage.get("damage_id") or "")
            damage_type = str(damage.get("damage_type") or "")
            damage["display_name_zh"] = (
                skill_name_renderer.resolve_damage_name(
                    damage_id,
                    ability_id=skill_id,
                )
                or damage_id
                or "倍率项"
            )
            damage["damage_type_name_zh"] = (
                skill_name_renderer.render_damage_type_name(damage_type)
            )
    awakenings = static_dao.list_character_awaken_effects(character_id)
    likeability_bonus = static_dao.get_character_likeability_bonus(character_id)
    forks = _compatible_forks(
        character,
        cached(
            ("fork_templates", str(static_database_path or "default")),
            static_dao.list_fork_templates,
        ),
    )
    saved_profile = user_dao.get_character_profile(character_id) if user_dao is not None else None
    profile = resolve_official_role_profile(
        character, growth_rows, forks, skills, awakenings,
        saved_profile=saved_profile,
        observation=(user_dao.get_native_character_profile_observation(character_id) if user_dao is not None else None),
        likeability_bonus=likeability_bonus,
    )
    inventory = (load_role_inventory_contexts(
        user_dao, static_dao, catalog, character, character_id, cached,
        include_candidates=include_replacement_candidates,
    ) if include_inventory_contexts else {})
    current_items = inventory.get("current_items", [])
    saved_plan = inventory.get("saved_plan")
    saved_items = inventory.get("saved_items", [])
    replacement_items = inventory.get("replacement_items", [])
    extra_saved_contexts = inventory.get("extra_saved_contexts", {})
    selected_slot = inventory.get("selected_slot")
    selected_slot_name = inventory.get("selected_slot_name", "")
    equipment_plan = static_dao.get_equipment_plan(character_id)
    static_shape_bonus = get_effective_character_shape_bonus(
        static_dao,
        character_id,
        shared_database_path=shared_database_path,
    ) or {}
    shape_bonus = static_shape_bonus
    current_calculation_items = project_equipment_items_to_max_level(
        current_items,
        static_dao,
    )
    saved_calculation_items = project_equipment_items_to_max_level(
        saved_items,
        static_dao,
    )
    for context in extra_saved_contexts.values():
        context["calculation_items"] = project_equipment_items_to_max_level(
            context["items"],
            static_dao,
        )
        context["available"] = bool(context["items"])
    graduation_template = static_dao.get_character_graduation_template(character_id)
    # 基础权重与实际角色身份独立解析；边际权重仍由当前角色面板生成。
    weight_record = (resolve_character_base_weights(user_dao, static_dao, character_id) if user_dao is not None
                     else static_dao.get_character_recommended_weights(character_id)) or {}
    world_bonus = WorldBonusSettings.from_payload(
        (user_dao.list_application_setting_copies().get(WORLD_BONUS_SETTING_KEY) if user_dao is not None else None)
    )
    weights = {
        str(key): float(value)
        for key, value in (weight_record.get("property_weights") or {}).items()
    }
    attributes = cached(
        ("equipment_attributes", str(static_database_path or "default")),
        lambda: {
            row["attribute_id"]: row
            for row in static_dao.list_equipment_attributes()
        },
    )
    equipment_items = cached(
        ("equipment_items", str(static_database_path or "default")),
        static_dao.list_equipment_items,
    )
    equipment_by_id = {
        str(row["item_id"]): row for row in equipment_items
    }
    graduation_extra_shape_count = graduation_extra_shape_drive_count(
        shape_bonus, equipment_plan, equipment_by_id,
    )
    item_names = {
        row["item_id"]: row.get("name_zh") or row["item_id"]
        for row in equipment_items
    }
    suit_names = cached(
        ("suit_names", str(static_database_path or "default")),
        lambda: {
            row["suit_id"]: row.get("name_zh") or row["suit_id"]
            for row in static_dao.list_suits()
        },
    )
    for item in saved_items:
        if not item.get("virtual"):
            continue
        item["names"] = {
            "zh_cn": item_names.get(
                item.get("item_id"), item.get("item_id") or "空装备"
            )
        }
        item["suit_names"] = {
            "zh_cn": suit_names.get(
                item.get("suit_id"),
                item.get("suit_id") or item["names"]["zh_cn"],
            )
        }
    item_icon_paths = cached(
        ("item_icon_paths", str(resolved_asset_root)),
        lambda: {
            str(row["item_id"]): icon_path
            for row in equipment_items
            if (
                icon_path := catalog.inventory_item_icon(
                    str(row.get("kind") or ""),
                    str(row["item_id"]),
                )
            ) is not None
        },
    )
    return {
        "character": character,
        "catalog_scope": catalog_scope,
        "resolved_static_database_path": resolved_static_database_path,
        "resolved_static_schema_version": resolved_static_schema_version,
        "profile": profile,
        "growth_rows": growth_rows,
        "skills": skills,
        "awakenings": awakenings,
        "likeability_bonus": likeability_bonus,
        "world_bonus": world_bonus,
        "forks": forks,
        "equipment_plan": equipment_plan,
        "shape_bonus": shape_bonus,
        "graduation_template": graduation_template,
        "graduation_extra_shape_count": graduation_extra_shape_count,
        "attributes": attributes,
        "item_names": item_names,
        "item_icon_paths": item_icon_paths,
        "weights": weights,
        "weight_record": weight_record,
        "replacement_items": replacement_items,
        "current_items": current_items,
        "current_calculation_items": current_calculation_items,
        "saved_items": saved_items,
        "saved_calculation_items": saved_calculation_items,
        "saved_plan": saved_plan,
        "selected_slot": selected_slot,
        "selected_slot_name": selected_slot_name,
        "extra_saved_contexts": extra_saved_contexts,
        "include_replacement_candidates": include_replacement_candidates,
        "icon_path": catalog.character_icon(character_id),
        "character_id": int(character_id),
    }
