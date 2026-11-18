# 计算官方角色配装的毕业度与属性收益。
"""Shared direct-damage graduation calculations for role and loadout views."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.domain.characters.recommended_weights import WORKSHOP_STAT_PROPERTY_IDS
from src.domain.localization.catalog import localized_text
from src.domain.equipment.properties.stat_catalog import StatCatalog
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.domain.official_role.graduation_shape import graduation_extra_shape_stats
from src.services.official_role.equipment.marginal_scoring import (
    calculate_official_role_damage, calculate_official_role_damage_batch,
)
from src.domain.official_role.models import WorldBonusSettings
from src.services.official_role.native_damage import role_damage_calculator

_WEIGHT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"),
    ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"),
    ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"),
    ("防御力", "DefAdd"),
    ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"),
    ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
)


@dataclass(frozen=True)
class OfficialRoleGraduationSummary:
    """One loadout graduation value and the role page's benchmark explanation."""

    rate: float | None
    tooltip: str


def _attribute_name(detail: dict, property_id: str) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    return str(
        attribute.get("display_name_zh")
        or attribute.get("filter_name_zh")
        or property_id
    )


def _stat_text(detail: dict, stat: dict) -> str:
    value = float(stat.get("value") or 0.0)
    if stat.get("percent"):
        value *= 100.0
    shown = f"{value:.2f}".rstrip("0").rstrip(".")
    suffix = "%" if stat.get("percent") else ""
    property_id = str(stat.get("property_id") or "")
    return f"{_attribute_name(detail, property_id)} {shown}{suffix}"


def _full_progression_detail(detail: dict, template: dict) -> dict | None:
    """Hold non-equipment progression fixed on both sides of the ratio."""

    profile = template.get("profile")
    if not isinstance(profile, dict) or not profile:
        return None
    return {
        **detail,
        "profile": dict(profile),
        "world_bonus": WorldBonusSettings().to_payload(),
    }


def graduation_template_with_weight_substats(detail: dict, *, calculator=None) -> dict | None:
    """Project the stored benchmark onto the strict runtime substat pool."""

    template = detail.get("graduation_template")
    if not isinstance(template, dict):
        return None
    if not isinstance(detail.get("property_weights"), dict):
        return dict(template)
    config_dir = detail.get("config_dir") or bundled_config_dir()
    frozen_catalog = detail.get("stat_catalog")
    catalog = frozen_catalog if isinstance(frozen_catalog, StatCatalog) else StatCatalog.from_config_dir(config_dir)
    labels_by_property = {
        property_id: label for label, property_id in _WEIGHT_PROPERTY_CHOICES
    }
    value_by_property = {
        property_id: label
        for property_id, label in labels_by_property.items()
        if label in catalog.tape_sub_stat_pool()
    }
    weights = {
        property_id: float(
            (detail.get("property_weights") or {}).get(property_id, 0.0)
        )
        for property_id in value_by_property
    }
    selected = sorted(weights, key=lambda key: (-weights[key], key))[:4]
    attributes = detail.get("attributes") or {}

    def stat(
        property_id: str,
        values: dict[str, float],
        multiplier: float,
    ) -> dict:
        label = value_by_property[property_id]
        percent = bool((attributes.get(property_id) or {}).get("show_percent"))
        raw_value = float(values[label]) * multiplier
        return {
            "property_id": property_id,
            "value": raw_value / 100.0 if percent else raw_value,
            "percent": percent,
        }

    equipment = [dict(item) for item in template.get("equipment") or ()]
    configured_extra_shape_count = detail.get("graduation_extra_shape_count")
    extra_shape_count = (
        int(configured_extra_shape_count)
        if configured_extra_shape_count is not None
        else int(template.get("extra_shape_count") or 0)
    )
    extra_shape_stats = graduation_extra_shape_stats(
        detail.get("shape_bonus"),
        extra_shape_count,
        attributes,
    )
    for item in equipment:
        if str(item.get("kind") or "") == "module":
            item["sub_stats"] = [
                stat(
                    property_id,
                    catalog.gold_base_values,
                    float(template.get("drive_area") or 20),
                )
                for property_id in selected
            ] + [dict(row) for row in extra_shape_stats]
        elif str(item.get("kind") or "") == "core":
            item["sub_stats"] = [
                stat(property_id, catalog.tape_stat_values, 1.0)
                for property_id in selected
            ]
    core = next(
        (item for item in equipment if str(item.get("kind") or "") == "core"),
        None,
    )
    main_weights = detail.get("main_property_weights") or detail.get(
        "property_weights"
    ) or {}
    main_candidates = sorted(
        (
            (
                -float(main_weights.get(property_id, 0.0)),
                property_id,
                label,
            )
            for label in catalog.tape_main_stat_pool()
            if (property_id := WORKSHOP_STAT_PROPERTY_IDS.get(label)) is not None
            and float(main_weights.get(property_id, 0.0)) > 0.0
        )
    )
    if core is not None and main_candidates:
        full_detail = _full_progression_detail(detail, template)
        if full_detail is None:
            return None
        best_damage = -1.0
        best_main: dict[str, Any] | None = None
        main_stats, details = [], []
        for _negative_weight, property_id, label in main_candidates:
            percent = bool((attributes.get(property_id) or {}).get("show_percent"))
            raw_value = float(catalog.tape_main_values[label])
            main_stat = {
                "property_id": property_id,
                "value": raw_value / 100.0 if percent else raw_value,
                "percent": percent,
            }
            candidate_equipment = [
                ({**item, "main_stats": [main_stat]} if item is core else item)
                for item in equipment
            ]
            candidate_detail = {
                **full_detail,
                "equipment_contexts": {
                    **(detail.get("equipment_contexts") or {}),
                    "graduation": {
                        "title": "直伤毕业基准",
                        "available": True,
                        "items": candidate_equipment,
                    },
                },
            }
            main_stats.append(main_stat)
            details.append(candidate_detail)
        damages = calculate_official_role_damage_batch(details, "graduation", calculator=calculator)
        for main_stat, candidate_damage in zip(main_stats, damages, strict=True):
            damage = float(candidate_damage or 0.0)
            if damage > best_damage:
                best_damage = damage
                best_main = main_stat
        if best_main is not None:
            core["main_stats"] = [best_main]
    return {**template, "equipment": equipment}


def graduation_benchmark_damage(detail: dict, *, calculator=None) -> float | None:
    """Calculate the same strict-substat graduation reference as the role page."""

    with role_damage_calculator(detail, calculator) as owned:
        template = graduation_template_with_weight_substats(detail, calculator=owned)
        if not isinstance(template, dict):
            return None
        return _benchmark_damage_for_template(detail, template, calculator=owned)


def _benchmark_damage_for_template(detail: dict, template: dict, *, calculator=None) -> float | None:
    full_detail = _full_progression_detail(detail, template)
    if full_detail is None:
        return None
    calculation_detail = {
        **full_detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            "graduation": {
                "title": "直伤毕业基准",
                "available": True,
                "items": template.get("equipment") or (),
            },
        },
    }
    damage = float(calculate_official_role_damage(calculation_detail, "graduation", calculator=calculator) or 0.0)
    return damage if damage > 0 else None


def graduation_rate(detail: dict, context_key: str, *, calculator=None) -> float | None:
    """Compare actual and benchmark equipment at the same full progression."""

    if calculator is None:
        with role_damage_calculator(detail) as owned:
            return graduation_rate(detail, context_key, calculator=owned)
    template = graduation_template_with_weight_substats(detail, calculator=calculator)
    if not isinstance(template, dict):
        return None
    full_detail = _full_progression_detail(detail, template)
    if full_detail is None:
        return None
    benchmark = _benchmark_damage_for_template(detail, template, calculator=calculator)
    damage = float(calculate_official_role_damage(full_detail, context_key, calculator=calculator) or 0.0)
    if damage <= 0 or not benchmark:
        return None
    return damage / benchmark * 100.0


def _resolved_graduation_context_key(detail: dict, context_key: str) -> str:
    """Resolve a saved-slot key to the role detail's selected saved context."""

    contexts = detail.get("equipment_contexts") or {}
    requested = str(context_key)
    if requested in contexts:
        return requested
    if not requested.startswith("saved:"):
        return requested
    try:
        slot_id = int(requested.removeprefix("saved:"))
    except ValueError:
        return requested
    saved = contexts.get("saved") or {}
    return "saved" if int(saved.get("slot_id") or 0) == slot_id else requested


def graduation_tooltip(detail: dict, catalog=None) -> str:
    """Describe the benchmark equipment behind the graduation percentage."""

    # 说明文案经冻结目录渲染；属性名是正式游戏数据，原样插入。
    def t(key, fallback, **values):
        return localized_text(catalog, f"official_role.graduation.tooltip.{key}", fallback, **values)

    if (detail.get("likeability_bonus") or {}).get("availability") == "unavailable":
        return t("likeability_missing", "满练度毕业基准需要十级好感属性；当前同版资料缺项，尚未生成该角色的毕业基准。")

    template = graduation_template_with_weight_substats(detail)
    if not isinstance(template, dict):
        return t("not_generated", "空幕直伤毕业基准尚未生成。")
    equipment = template.get("equipment") or ()
    if not isinstance(equipment, (list, tuple)):
        return t("not_generated", "空幕直伤毕业基准尚未生成。")
    core: dict[str, Any] = next(
        (
            item
            for item in equipment
            if str(item.get("kind") or "") == "core"
        ),
        {},
    )
    main: dict[str, Any] = next(iter(core.get("main_stats") or ()), {})
    main_text = _stat_text(detail, main) if main else t("not_recorded", "未记录")
    aggregated_substats: dict[tuple[str, bool], dict] = {}
    for item in equipment:
        for stat in item.get("sub_stats") or ():
            key = (
                str(stat.get("property_id") or ""),
                bool(stat.get("percent")),
            )
            if key not in aggregated_substats:
                aggregated_substats[key] = dict(stat)
                continue
            aggregated_substats[key]["value"] = (
                float(aggregated_substats[key].get("value") or 0.0)
                + float(stat.get("value") or 0.0)
            )
    substat_text = [
        _stat_text(detail, stat) for stat in aggregated_substats.values()
    ]
    substat_lines = [
        "、".join(substat_text[index:index + 3])
        for index in range(0, len(substat_text), 3)
    ]
    lines = [
        t("heading", "空幕直伤毕业基准（满级角色、满级精1弧盘）："),
        t("main_stat", "卡带主词条：{value}", value=main_text),
        t("substats", "毕业副词条：{value}", value=substat_lines[0] if substat_lines else t("not_recorded", "未记录")),
    ]
    lines.extend(f"　　　　　{line}" for line in substat_lines[1:])
    lines.extend((
        t("formula", "毕业率 = 满练度配当前空幕直伤 ÷ 满练度配毕业空幕直伤，结果不封顶。"),
        t("fixed_inputs", "两侧均固定满级养成、精1弧盘常驻、好感10与家具满加成。"),
        t("scope", "只计算直伤，不计条件被动、机制伤害和队友加成。"),
    ))
    return "\n".join(lines)


def load_official_role_graduation_rate(
    user_database_path: str | Path,
    character_id: int,
    *,
    context_key: str,
    asset_root: str | Path | None = None,
) -> float | None:
    """Load one account-pinned role and calculate its requested loadout rate."""

    return load_official_role_graduation_summary(
        user_database_path,
        character_id,
        context_key=context_key,
        asset_root=asset_root,
    ).rate


def load_official_role_graduation_summary(
    user_database_path: str | Path,
    character_id: int,
    *,
    context_key: str,
    asset_root: str | Path | None = None,
) -> OfficialRoleGraduationSummary:
    """Load the value and exact benchmark explanation shared with the role page."""

    from src.services.official_role.page import load_official_role_detail

    detail = load_official_role_detail(
        user_database_path,
        int(character_id),
        asset_root=asset_root,
        include_inventory_contexts=True,
    )
    return OfficialRoleGraduationSummary(
        rate=graduation_rate(
            detail,
            _resolved_graduation_context_key(detail, str(context_key)),
        ),
        tooltip=graduation_tooltip(detail),
    )
