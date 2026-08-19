# 将官方静态资料与账号指针投影为新角色页面模型。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Mapping

from src.domain.official_role import (
    OFFICIAL_ROLE_TAB_ORDER_SCOPE,
    OfficialAttributeSummaryValue,
)
from src.services.character_shape_bonus_service import get_effective_character_shape_bonus
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.graduation_bonus_service import graduation_extra_shape_drive_count
from src.services.inventory_source_capabilities import is_visual_inventory_source
from src.services.official_role_awakening_service import resolve_awakening_profile
from src.services.skill_name_rendering_service import SkillNameRenderingService
from src.services.damage_calculation_service import (
    DamageCalculationService,
    DamageScalingStat,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao

__all__ = [
    "OfficialAttributeSummaryValue",
    "calculate_official_role_attribute_summaries",
    "calculate_official_role_damage_breakdown",
    "calculate_official_role_equipment_gain",
    "calculate_official_role_final_weights",
    "calculate_official_role_hidden_equipment_score",
    "calculate_official_role_item_gain",
    "calculate_official_role_margins",
    "load_official_role_detail",
    "load_official_role_index",
    "replacement_candidates_for_official_role",
    "save_official_role_replacement",
    "save_official_role_tab_order",
]

from src.services.official_role_attribute_service import (
    _asset_root,
    _compatible_forks,
    _default_profile,
    _theory_properties,
    _resolved_plan_items,
    calculate_official_role_attribute_summaries,
    _context_calculation_items,
    _equipment_property_stats,
    _property_stats_by_source,
    _element_damage_property,
    _role_panel_damage_inputs,
    calculate_official_role_equipment_gain,
)


from src.services.official_role_replacement_service import (
    calculate_official_role_item_gain,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
)


from src.services.official_role_scoring_service import (
    calculate_official_role_margins,
    calculate_official_role_final_weights,
    calculate_official_role_hidden_equipment_score,
)


from src.services.official_role_labels import (
    _property_label,
)


def calculate_official_role_damage_breakdown(
    detail: Mapping[str, Any], context_key: str,
) -> dict[str, Any] | None:
    """Explain every factor of the role panel's normalized 100% direct hit."""

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    values = inputs[0]
    result = DamageCalculationService.calculate_direct(values)
    fork_stats, equipment_stats, combined_stats = _property_stats_by_source(
        detail, context_key
    )
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    scaling_names = {
        DamageScalingStat.ATTACK: "攻击力",
        DamageScalingStat.HEALTH: "生命值",
        DamageScalingStat.DEFENSE: "防御力",
    }
    scaling_components = {
        DamageScalingStat.ATTACK: (
            values.attack_base, values.attack_up, values.attack_add,
        ),
        DamageScalingStat.HEALTH: (
            values.health_base, values.health_up, values.health_add,
        ),
        DamageScalingStat.DEFENSE: (
            values.defense_base, values.defense_up, values.defense_add,
        ),
    }
    scaling_base, scaling_up, scaling_add = scaling_components[values.scaling_stat]

    damage_sources: list[dict[str, Any]] = []
    for property_id in ("DamageUpGeneralBase", "DamageUpGeneralAdd", element_property):
        if property_id and combined_stats.get(property_id, 0.0):
            damage_sources.append({
                "label": _property_label(detail, property_id),
                "value": combined_stats[property_id],
            })

    bonuses = [
        {
            "source": "角色基础", "label": "生命值",
            "value": values.health_base - combined_stats.get("HPMaxBase", 0.0),
            "percent": False,
        },
        {
            "source": "角色基础", "label": "攻击力",
            "value": values.attack_base - combined_stats.get("AtkBase", 0.0),
            "percent": False,
        },
        {
            "source": "角色基础", "label": "防御力",
            "value": values.defense_base - combined_stats.get("DefBase", 0.0),
            "percent": False,
        },
        {"source": "角色基础", "label": "暴击率", "value": 0.05, "percent": True},
        {"source": "角色基础", "label": "暴击伤害", "value": 0.50, "percent": True},
    ]
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    raw_equipment_stats = _equipment_property_stats(
        detail,
        _context_calculation_items(context),
        include_shape_bonus=False,
    )
    shape_stats = {
        property_id: value - raw_equipment_stats.get(property_id, 0.0)
        for property_id, value in equipment_stats.items()
        if value - raw_equipment_stats.get(property_id, 0.0)
    }
    for source, source_stats in (
        ("弧盘", fork_stats),
        ("空幕/驱动", raw_equipment_stats),
        ("额外形状", shape_stats),
    ):
        for property_id, value in source_stats.items():
            if value:
                attribute = (detail.get("attributes") or {}).get(property_id) or {}
                bonuses.append({
                    "source": source,
                    "label": _property_label(detail, property_id),
                    "property_id": property_id,
                    "value": value,
                    "percent": bool(attribute.get("show_percent")),
                })

    factor_rows = [
        {
            "name": "技能伤害倍率",
            "value": values.skill_multiplier,
            "detail": "角色面板统一按 100% 计算",
        },
        {
            "name": f"倍率对应属性（{scaling_names[values.scaling_stat]}）",
            "value": result.scaling_attribute_value,
            "detail": (
                f"{scaling_base:g} × (1 + {scaling_up * 100:g}%) + "
                f"{scaling_add:g}"
            ),
        },
        {
            "name": "增伤区",
            "value": result.damage_increase_multiplier,
            "detail": "1 + " + (
                " + ".join(
                    f"{item['label']} {float(item['value']) * 100:g}%"
                    for item in damage_sources
                ) or "0%"
            ),
        },
        {
            "name": "暴击区",
            "value": result.critical_multiplier,
            "detail": f"1 + {values.crit_rate * 100:g}% × {values.crit_damage * 100:g}%",
        },
        {
            "name": "防御区",
            "value": result.defense_multiplier,
            "detail": (
                f"敌防 {result.enemy_defense:g}；角色 {values.character_level:g} 级 / "
                f"敌人 {values.enemy_level:g} 级；穿透 {values.defense_penetration * 100:g}% / "
                f"减防 {values.defense_reduction * 100:g}%"
            ),
        },
        {
            "name": "抗性区",
            "value": result.resistance_multiplier,
            "detail": (
                f"基础抗性 {values.boss_resistance * 100:g}% - 减抗 "
                f"{sum(values.enemy_resistance_reductions) * 100:g}% - 穿透 "
                f"{sum(values.resistance_penetrations) * 100:g}% = "
                f"{result.effective_resistance * 100:g}%"
            ),
        },
        {
            "name": "易伤区",
            "value": result.vulnerability_multiplier,
            "detail": "1 + " + (
                " + ".join(f"{value * 100:g}%" for value in values.vulnerability_increases)
                or "0%"
            ),
        },
        {
            "name": "独立乘区",
            "value": result.independent_multiplier,
            "detail": " × ".join(
                f"(1 + {value * 100:g}%)" for value in values.independent_damage_bonuses
            ) or "暂无独立增伤，乘区为 1",
        },
    ]
    return {
        "context_key": context_key,
        "damage": result.damage,
        "scaling_stat": values.scaling_stat.value,
        "element_property_id": element_property,
        "bonuses": bonuses,
        "factors": factor_rows,
        "formula_values": tuple(row["value"] for row in factor_rows),
    }


def load_official_role_index(
    user_database_path: str | Path,
    *,
    asset_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """List official playable roles, ordered by account pointers when present."""

    catalog = GameUiAssetCatalog(_asset_root(asset_root))
    with StaticGameDataDao() as static_dao, UserDataDao(user_database_path) as user_dao:
        profiles = {row["character_id"]: row for row in user_dao.list_character_profiles()}
        preferred_character_ids = [
            *user_dao.list_observed_character_ids(),
            *profiles,
        ]
        characters = static_dao.list_role_template_characters(
            preferred_character_ids,
        )
        saved_order = user_dao.get_ui_item_order(OFFICIAL_ROLE_TAB_ORDER_SCOPE)
    saved_rank: dict[int, int] = {}
    for ordinal, item_key in enumerate(saved_order):
        try:
            character_id = int(item_key)
        except (TypeError, ValueError):
            continue
        saved_rank.setdefault(character_id, ordinal)
    return sorted(
        [
            {
                **character,
                "icon_path": catalog.character_icon(int(character["character_id"])),
                "persisted": int(character["character_id"]) in profiles,
                "ordinal": profiles.get(int(character["character_id"]), {}).get("ordinal", 10000),
            }
            for character in characters
        ],
        key=lambda row: (
            0 if int(row["character_id"]) in saved_rank else 1,
            saved_rank.get(
                int(row["character_id"]),
                int(row["ordinal"]),
            ),
            int(row["character_id"]),
        ),
    )


def save_official_role_tab_order(
    user_database_path: str | Path,
    character_ids: Sequence[int],
) -> list[int]:
    """Persist the character page's visual tab order for the current account."""

    normalized = [int(character_id) for character_id in character_ids]
    if any(character_id <= 0 for character_id in normalized):
        raise ValueError("角色 Tab 顺序包含无效 character_id")
    if len(set(normalized)) != len(normalized):
        raise ValueError("角色 Tab 顺序不能包含重复角色")
    with UserDataDao(user_database_path) as user_dao:
        user_dao.replace_ui_item_order(
            OFFICIAL_ROLE_TAB_ORDER_SCOPE,
            normalized,
        )
    return normalized


def _display_loadout_slot_name(
    character: Mapping[str, Any],
    slot: Mapping[str, Any],
) -> str:
    """Keep an unrenamed primary slot labeled with its character name."""

    slot_name = str(slot.get("slot_name") or "").strip()
    if (
        str(slot.get("slot_key") or "") == "primary"
        and slot_name == "主力"
    ):
        return str(character.get("name_zh") or slot_name)
    return slot_name


def _mark_equipment_level_known(
    items: Sequence[dict[str, Any]],
    snapshot_source: object,
) -> None:
    """Carry a snapshot's observable level capability into role-card views."""

    level_known = not is_visual_inventory_source(snapshot_source)
    for item in items:
        item["level_known"] = level_known


def load_official_role_detail(
    user_database_path: str | Path,
    character_id: int,
    *,
    asset_root: str | Path | None = None,
    include_inventory_contexts: bool = True,
    static_database_path: str | Path | None = None,
    shared_database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one page model from static SQLite plus account SQLite pointers.

    Result calculation only needs the role's panel model; it supplies its own
    pinned items.  ``include_inventory_contexts=False`` avoids loading a saved
    plan's whole snapshot merely to render an unrelated allocation result.
    """

    catalog = GameUiAssetCatalog(_asset_root(asset_root))
    with (
        StaticGameDataDao(static_database_path) as static_dao,
        UserDataDao(user_database_path) as user_dao,
    ):
        character = static_dao.get_character(character_id)
        if character is None:
            raise ValueError(f"官方角色不存在：{character_id}")
        growth_rows = static_dao.list_character_panel_growth(character_id)
        skills = static_dao.list_character_skills(character_id)
        skill_name_renderer = SkillNameRenderingService.from_static_dao(static_dao)
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
        forks = _compatible_forks(character, static_dao.list_fork_templates())
        saved_profile = user_dao.get_character_profile(character_id)
        profile = dict(saved_profile) if saved_profile else _default_profile(
            character, growth_rows, forks, skills, awakenings, 0
        )
        if saved_profile is None:
            profile["likeability_level_10_enabled"] = likeability_bonus is not None
        profile = resolve_awakening_profile(profile, awakenings)
        profile["persisted"] = saved_profile is not None
        current_items: list[dict[str, Any]] = []
        saved_plan: Mapping[str, Any] | None = None
        saved_items: list[dict[str, Any]] = []
        replacement_items: list[dict[str, Any]] = []
        extra_saved_contexts: dict[str, dict[str, Any]] = {}
        selected_slot: Mapping[str, Any] | None = None
        selected_slot_name = ""
        if include_inventory_contexts:
            current_snapshot_id = user_dao.current_inventory_snapshot_id()
            current_snapshot = (
                user_dao.inventory_snapshot_summary(current_snapshot_id)
                if current_snapshot_id is not None
                else None
            )
            current_items = user_dao.list_current_inventory_items(
                equipped=True, character_id=character_id
            )
            _mark_equipment_level_known(
                current_items,
                (current_snapshot or {}).get("source"),
            )
            slot_plans = [
                row
                for row in user_dao.list_current_loadout_slot_plans()
                if int(row["slot"]["character_id"]) == character_id
            ]
            slot_plans.sort(
                key=lambda row: (
                    str(row["slot"].get("slot_key") or "") != "primary",
                    int(row["slot"].get("sort_order") or 0),
                    int(row["slot"]["slot_id"]),
                )
            )
            selected_slot = slot_plans[0] if slot_plans else None
            saved_plan = selected_slot["plan"] if selected_slot is not None else None
            selected_slot_name = (
                _display_loadout_slot_name(character, selected_slot["slot"])
                if selected_slot is not None
                else ""
            )
            replacement_items = (
                user_dao.list_inventory_items(int(saved_plan["source_snapshot_id"]))
                if saved_plan and saved_plan.get("source_snapshot_id") is not None
                else []
            )
            saved_snapshot = (
                user_dao.inventory_snapshot_summary(
                    int(saved_plan["source_snapshot_id"])
                )
                if saved_plan and saved_plan.get("source_snapshot_id") is not None
                else None
            )
            _mark_equipment_level_known(
                replacement_items,
                (saved_snapshot or {}).get("source"),
            )
            saved_items = _resolved_plan_items(
                user_dao,
                saved_plan,
                snapshot_items=replacement_items,
            )
            for row in slot_plans[1:]:
                slot = row["slot"]
                plan = row["plan"]
                source_snapshot_id = plan.get("source_snapshot_id")
                slot_items = (
                    user_dao.list_inventory_items(int(source_snapshot_id))
                    if source_snapshot_id is not None
                    else []
                )
                slot_snapshot = (
                    user_dao.inventory_snapshot_summary(int(source_snapshot_id))
                    if source_snapshot_id is not None
                    else None
                )
                _mark_equipment_level_known(
                    slot_items,
                    (slot_snapshot or {}).get("source"),
                )
                slot_display_name = _display_loadout_slot_name(character, slot)
                extra_saved_contexts[f"saved:{slot['slot_id']}"] = {
                    "title": f"已保存配装 · {slot_display_name}",
                    "items": _resolved_plan_items(
                        user_dao,
                        plan,
                        snapshot_items=slot_items,
                    ),
                    "calculation_items": (),
                    "plan": plan,
                    "replacement_items": slot_items,
                    "slot_id": int(slot["slot_id"]),
                    "slot_name": slot_display_name,
                }
            characters = {
                int(row["character_id"]): row
                for row in static_dao.list_characters()
            }
            owner_by_uid: dict[tuple[int, int], int] = {}
            for row in user_dao.list_current_loadout_equipment_owners():
                owner_by_uid.setdefault(
                    (int(row["uid_slot"]), int(row["uid_serial"])),
                    int(row["character_id"]),
                )
            locked_uids = {
                (int(row["uid_slot"]), int(row["uid_serial"]))
                for row in user_dao.list_allocation_locked_equipment_owners()
            }
            candidate_pools = [replacement_items, *(
                context["replacement_items"]
                for context in extra_saved_contexts.values()
                if isinstance(context.get("replacement_items"), list)
            )]
            for candidate_pool in candidate_pools:
                for item in candidate_pool:
                    uid = (int(item["uid_slot"]), int(item["uid_serial"]))
                    item["allocation_reserved"] = uid in locked_uids
                    owner_id = owner_by_uid.get(uid)
                    item["equipped"] = False
                    item["equipped_character_id"] = None
                    item["equipped_character_name"] = ""
                    item.pop("equipped_character_icon_path", None)
                    if owner_id is None:
                        continue
                    owner = characters.get(owner_id) or {}
                    item["equipped"] = True
                    item["equipped_character_id"] = owner_id
                    item["equipped_character_name"] = str(
                        owner.get("name_zh") or owner_id
                    )
                    owner_icon = catalog.character_icon(owner_id)
                    if owner_icon is not None:
                        item["equipped_character_icon_path"] = str(owner_icon)
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
        # 角色页只读显示当前账号在“权重”页已保存的基础权重；角色页本身
        # 绝不写回它。账号尚未生成该角色记录时，才回落公共默认。
        public_weight_record = (
            static_dao.get_character_recommended_weights(character_id) or {}
        )
        account_weight_record = user_dao.get_character_weight_preferences(character_id)
        weight_record = account_weight_record or public_weight_record
        weights = {
            str(key): float(value)
            for key, value in (weight_record.get("property_weights") or {}).items()
        }
        attributes = {
            row["attribute_id"]: row for row in static_dao.list_equipment_attributes()
        }
        equipment_items = static_dao.list_equipment_items()
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
        suit_names = {
            row["suit_id"]: row.get("name_zh") or row["suit_id"]
            for row in static_dao.list_suits()
        }
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
        item_icon_paths = {
            str(row["item_id"]): icon_path
            for row in equipment_items
            if (
                icon_path := catalog.inventory_item_icon(
                    str(row.get("kind") or ""),
                    str(row["item_id"]),
                )
            ) is not None
        }
    theory_ids = _theory_properties(weights)
    has_saved_weights = any(float(value) > 0 for value in weights.values())
    theory_weights = {
        property_id: float(weights.get(property_id, 0.0)) if has_saved_weights else 1.0
        for property_id in theory_ids
    }
    main_ids = tuple((equipment_plan or {}).get("core_attribute_ids") or ())
    return {
        "character": character,
        "icon_path": catalog.character_icon(character_id),
        "profile": profile,
        "growth_rows": growth_rows,
        "skills": skills,
        "awakenings": awakenings,
        "likeability_bonus": likeability_bonus,
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
            (account_weight_record or {}).get("source_kind") or "default"
        ),
        "property_weights_from_account": account_weight_record is not None,
        "theory_weights": theory_weights,
        "theory_weights_persisted": account_weight_record is not None,
        "replacement_items": replacement_items,
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
