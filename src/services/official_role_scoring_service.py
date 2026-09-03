# 计算官方角色的边际收益、最终权重和隐藏装备评分。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import math
from pathlib import Path
from typing import Any, Mapping

from src.domain.official_role import ROLE_PANEL_MARGINAL_UNITS
from src.integrations.bundled_resources import bundled_config_dir
from src.services.virtual_equipment_service import (
    grid_count_from_geometry,
)
from src.optimizer.scoring import ScoringEngine
from src.services.equipment_scoring_service import score_drive_stats, score_tape_stats
from src.services.damage_calculation_service import (
    DamageScalingStat,
)


from src.services.official_role_attribute_service import (
    _element_damage_property,
    _property_stats_by_source,
    _role_panel_damage_inputs,
    _total_direct_damage,
)
from src.services.official_role_labels import _property_label

def calculate_official_role_margins(detail: Mapping[str, Any], context_key: str) -> dict[str, Any] | None:
    """Recalculate the old role-panel marginal table with the new direct-damage formula."""

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    _fork_stats, _equipment_stats, combined_stats = _property_stats_by_source(
        detail, context_key
    )
    base_damage = _total_direct_damage(inputs)
    if base_damage <= 0:
        return None
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    candidates = [
        ("CritBase", "暴击率%", "crit_rate", ROLE_PANEL_MARGINAL_UNITS["CritBase"], True),
        (
            "CritDamageBase", "暴击伤害%", "crit_damage",
            ROLE_PANEL_MARGINAL_UNITS["CritDamageBase"], True,
        ),
        (
            "DamageUpGeneralBase", "伤害增加%", "damage_increases",
            ROLE_PANEL_MARGINAL_UNITS["DamageUpGeneralBase"], True,
        ),
    ]
    scaling_candidates = {
        DamageScalingStat.ATTACK: (
            ("AtkUp", "攻击力%", "attack_up", ROLE_PANEL_MARGINAL_UNITS["AtkUp"], True),
            ("AtkAdd", "攻击力", "attack_add", ROLE_PANEL_MARGINAL_UNITS["AtkAdd"], False),
        ),
        DamageScalingStat.HEALTH: (
            ("HPMaxUp", "生命值%", "health_up", ROLE_PANEL_MARGINAL_UNITS["HPMaxUp"], True),
            ("HPMaxAdd", "生命值", "health_add", ROLE_PANEL_MARGINAL_UNITS["HPMaxAdd"], False),
        ),
        DamageScalingStat.DEFENSE: (
            ("DefUp", "防御力%", "defense_up", ROLE_PANEL_MARGINAL_UNITS["DefUp"], True),
            ("DefAdd", "防御力", "defense_add", ROLE_PANEL_MARGINAL_UNITS["DefAdd"], False),
        ),
    }
    candidates.extend(scaling_candidates.get(inputs[0].scaling_stat, ()))
    if element_property:
        candidates.append((
            element_property, "异能伤害%", "damage_increases",
            ROLE_PANEL_MARGINAL_UNITS["ElementDamage"], True,
        ))
    rows: list[dict[str, Any]] = []
    for property_id, label, field, unit, is_percent in candidates:
        # 异能伤害属于角色固有边际项；即使用户没有把它作为配装权重，
        # 也必须展示对应元素的 1.25% 单位收益。
        updated = []
        for item in inputs:
            if field == "damage_increases":
                updated.append(replace(
                    item, damage_increases=(*item.damage_increases, unit),
                ))
            else:
                updated.append(replace(item, **{field: getattr(item, field) + unit}))
        next_damage = _total_direct_damage(tuple(updated))
        if property_id == "DamageUpGeneralBase":
            current_value = (
                combined_stats.get("DamageUpGeneralBase", 0.0)
                + combined_stats.get("DamageUpGeneralAdd", 0.0)
            )
        elif field == "damage_increases":
            current_value = combined_stats.get(property_id, 0.0)
        else:
            current_value = float(getattr(inputs[0], field))
        rows.append({
            "property_id": property_id,
            "label": label,
            "current_value": current_value,
            "unit": unit,
            "is_percent": is_percent,
            "next_damage": next_damage,
            "gain_percent": (next_damage / base_damage - 1.0) * 100.0,
        })
    rows.sort(key=lambda row: -float(row["gain_percent"]))
    return {
        "damage": base_damage,
        "rows": rows,
        "context_key": context_key,
        "warning": "弧盘无条件常驻属性已按当前精炼计入；条件被动沿用战斗状态计算。",
    }


def _normalized_direct_damage_weights(
    base_weights: Mapping[str, float], margins: Mapping[str, Any] | None,
) -> tuple[dict[str, float], frozenset[str]]:
    """Build the role page's read-only final weights from marginal damage."""

    weights = {str(key): float(value) for key, value in base_weights.items()}
    rows = list((margins or {}).get("rows") or ())
    formula_ids = frozenset(
        str(row.get("property_id") or "") for row in rows
    ) - {""}
    positive_gains = [
        float(row.get("gain_percent") or 0.0)
        for row in rows
        if math.isfinite(float(row.get("gain_percent") or 0.0))
        and float(row.get("gain_percent") or 0.0) > 0.0
    ]
    maximum_gain = max(positive_gains, default=0.0)
    for row in rows:
        property_id = str(row.get("property_id") or "")
        if not property_id:
            continue
        gain = float(row.get("gain_percent") or 0.0)
        weights[property_id] = (
            max(0.0, gain) / maximum_gain
            if maximum_gain > 0.0 and math.isfinite(gain)
            else 0.0
        )
    return weights, formula_ids


@lru_cache(maxsize=4)
def _replacement_scoring_engine(config_dir: str) -> ScoringEngine:
    """Reuse stat-name normalization while a replacement dialog is open."""

    return ScoringEngine(config_dir=config_dir, roles_db={})


def calculate_official_role_final_weights(
    detail: Mapping[str, Any],
    context_key: str,
    *,
    margins: Mapping[str, Any] | None = None,
    base_property_weights: Mapping[str, float] | None = None,
    base_main_property_weights: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Resolve the exact final weights displayed by the role-page table.

    Formula properties use current marginal direct-damage ratios; properties
    absent from the formula retain the account's base weight.  Both role-page
    and weighted-allocation replacement flows call this function so their
    hidden candidate score cannot drift from the visible table.
    """

    resolved_margins = margins if margins is not None else calculate_official_role_margins(
        detail, context_key,
    )
    property_weights, formula_ids = _normalized_direct_damage_weights(
        base_property_weights or detail.get("property_weights") or {},
        resolved_margins,
    )
    main_property_weights, _unused = _normalized_direct_damage_weights(
        base_main_property_weights or detail.get("main_property_weights") or {},
        resolved_margins,
    )
    return {
        "property_weights": property_weights,
        "main_property_weights": main_property_weights,
        "formula_property_ids": formula_ids,
        "margins": resolved_margins,
    }


def calculate_official_role_hidden_equipment_score(
    detail: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    property_weights: Mapping[str, float],
    main_property_weights: Mapping[str, float] | None = None,
    config_dir: str | Path | None = None,
) -> float:
    """Score one candidate with the role page's final weights, not direct damage.

    This is intentionally the same category/quality/shape scoring model used
    by result cards.  It is a hidden ordering metric for replacement choices;
    direct-damage gain remains a separate display-only metric.
    """

    configured_dir = config_dir or detail.get("config_dir")
    resolved_config_dir = Path(configured_dir) if configured_dir else bundled_config_dir()
    engine = _replacement_scoring_engine(str(resolved_config_dir))
    weights = {
        _property_label(detail, str(property_id)): float(value)
        for property_id, value in property_weights.items()
    }
    main_weights = (
        {
            _property_label(detail, str(property_id)): float(value)
            for property_id, value in main_property_weights.items()
        }
        if isinstance(main_property_weights, Mapping)
        else None
    )
    quality = {
        "orange": "Gold", "gold": "Gold", "purple": "Purple", "blue": "Blue",
    }.get(str(item.get("quality") or "").casefold(), "Gold")
    sub_stat_names = tuple(
        _property_label(detail, str(stat.get("property_id") or ""))
        for stat in item.get("sub_stats") or ()
    )
    if str(item.get("kind") or "") == "core":
        main_stats = tuple(item.get("main_stats") or ())
        first_main_stat = (
            main_stats[0]
            if main_stats and isinstance(main_stats[0], Mapping)
            else {}
        )
        raw_main_value = first_main_stat.get("value")
        try:
            main_value = float(raw_main_value) if raw_main_value is not None else None
        except (TypeError, ValueError):
            main_value = None
        return score_tape_stats(
            engine,
            main_stat_name=_property_label(
                detail,
                str(first_main_stat.get("property_id") or ""),
            ),
            sub_stat_names=sub_stat_names,
            weights=weights,
            quality=quality,
            main_weights=main_weights,
            main_value=main_value,
        )
    area = int(item.get("grid_count") or 0) or grid_count_from_geometry(
        item.get("geometry")
    )
    return score_drive_stats(
        engine,
        sub_stat_names=sub_stat_names,
        area=area,
        weights=weights,
        quality=quality,
    )
