# 计算官方角色的边际收益、最终权重及详情与替换共用的装备评分。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
import math
from pathlib import Path
from typing import Any, Mapping

from src.domain.localization.catalog import TextCatalog, localized_text
from src.domain.official_role.models import ROLE_PANEL_MARGINAL_UNITS
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.domain.equipment.loadouts.virtual_equipment import grid_count_from_geometry
from src.optimizer.scoring import ScoringEngine
from src.services.damage.damage_calculation_service import DamageScalingStat


from src.services.official_role.panel.attributes import _element_damage_property, _property_stats_by_source, _role_panel_damage_inputs, _total_direct_damage
from src.services.official_role.labels import _property_label

from src.services.official_role.native_damage import role_damage_calculator


# 角色页边际文案统一登记在 official_role.marginal 前缀下；无目录时回退下列中文基线。
_PREFIX = "official_role.marginal"


def _text(text_catalog: TextCatalog | None, suffix: str, fallback: str, **values: object) -> str:
    """按边际文案前缀取词；目录未注入或未登记该 key 时逐字回退原中文。"""

    return localized_text(text_catalog, f"{_PREFIX}.{suffix}", fallback, **values)


def calculate_official_role_margins(
    detail: Mapping[str, Any], context_key: str, *, calculator=None,
    text_catalog: TextCatalog | None = None,
) -> dict[str, Any] | None:
    """Recalculate the old role-panel marginal table with the new direct-damage formula."""

    if calculator is None:
        with role_damage_calculator(detail) as owned:
            return calculate_official_role_margins(
                detail, context_key, calculator=owned, text_catalog=text_catalog,
            )
    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    _fork_stats, _equipment_stats, combined_stats = _property_stats_by_source(
        detail, context_key
    )
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    candidates = [
        (
            "CritBase", _text(text_catalog, "label.crit_rate", "暴击率%"), "crit_rate",
            ROLE_PANEL_MARGINAL_UNITS["CritBase"], True,
        ),
        (
            "CritDamageBase", _text(text_catalog, "label.crit_damage", "暴击伤害%"),
            "crit_damage", ROLE_PANEL_MARGINAL_UNITS["CritDamageBase"], True,
        ),
        (
            "DamageUpGeneralBase", _text(text_catalog, "label.damage_up", "伤害增加%"),
            "damage_increases", ROLE_PANEL_MARGINAL_UNITS["DamageUpGeneralBase"], True,
        ),
    ]
    scaling_candidates = {
        DamageScalingStat.ATTACK: (
            ("AtkUp", _text(text_catalog, "label.attack_up", "攻击力%"), "attack_up",
             ROLE_PANEL_MARGINAL_UNITS["AtkUp"], True),
            ("AtkAdd", _text(text_catalog, "label.attack_add", "攻击力"), "attack_add",
             ROLE_PANEL_MARGINAL_UNITS["AtkAdd"], False),
        ),
        DamageScalingStat.HEALTH: (
            ("HPMaxUp", _text(text_catalog, "label.health_up", "生命值%"), "health_up",
             ROLE_PANEL_MARGINAL_UNITS["HPMaxUp"], True),
            ("HPMaxAdd", _text(text_catalog, "label.health_add", "生命值"), "health_add",
             ROLE_PANEL_MARGINAL_UNITS["HPMaxAdd"], False),
        ),
        DamageScalingStat.DEFENSE: (
            ("DefUp", _text(text_catalog, "label.defense_up", "防御力%"), "defense_up",
             ROLE_PANEL_MARGINAL_UNITS["DefUp"], True),
            ("DefAdd", _text(text_catalog, "label.defense_add", "防御力"), "defense_add",
             ROLE_PANEL_MARGINAL_UNITS["DefAdd"], False),
        ),
    }
    candidates.extend(scaling_candidates.get(inputs[0].scaling_stat, ()))
    if element_property:
        candidates.append((
            element_property, _text(text_catalog, "label.element_damage", "异能伤害%"),
            "damage_increases",
            ROLE_PANEL_MARGINAL_UNITS["ElementDamage"], True,
        ))
    groups = [inputs]
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
        groups.append(tuple(updated))
    base_damage, *next_values = calculator.totals(groups)
    if base_damage <= 0:
        return None
    rows: list[dict[str, Any]] = []
    for (property_id, label, field, unit, is_percent), next_damage in zip(candidates, next_values, strict=True):
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
        "warning": _text(
            text_catalog, "warning.resident_fork",
            "弧盘无条件常驻属性已按当前精炼计入；条件被动沿用战斗状态计算。",
        ),
    }


def calculate_official_role_damage(
    detail: Mapping[str, Any], context_key: str, *, calculator=None,
) -> float | None:
    """Return only the direct-damage total when marginal rows are not needed.

    Graduation and replacement ranking often need the same frozen formula total,
    not the full one-row-per-property sensitivity table. Keeping this narrow
    path beside the marginal calculation preserves the exact formula and avoids
    constructing/perturbing unused candidates.
    """

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    damage = _total_direct_damage(inputs, detail, calculator)
    return damage if damage > 0 else None



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
    text_catalog: TextCatalog | None = None,
    calculator=None,
) -> dict[str, Any]:
    """Resolve the exact final weights displayed by the role-page table.

    Formula properties use current marginal direct-damage ratios; properties
    absent from the formula retain the account's base weight.  Both role-page
    and weighted-allocation replacement flows call this function so their
    hidden candidate score cannot drift from the visible table.
    """

    # 调用方已持有计算器时复用同一冻结身份，不为回退重算再做一次整库校验或启动新分析进程。
    resolved_margins = margins if margins is not None else calculate_official_role_margins(
        detail, context_key, calculator=calculator, text_catalog=text_catalog,
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


def _hidden_equipment_score_inputs(
    detail: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    property_weights: Mapping[str, float],
    main_property_weights: Mapping[str, float] | None = None,
    config_dir: str | Path | None = None,
    static_database_path: str | Path | None = None,
):
    """Score one candidate with the role page's final weights, not direct damage.

    This is intentionally the same category/quality/shape scoring model used
    by result cards.  Role details display this score and its grade; replacement
    choices also use it for ordering. Direct-damage gain is a separate metric.
    """

    configured_dir = config_dir or detail.get("config_dir")
    resolved_config_dir = Path(configured_dir) if configured_dir else bundled_config_dir()
    source_path = static_database_path or detail.get("static_database_path")
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
        # 边际权重改变评分权重，不改变按品质满级的装备评分口径。
        # 实际主词条与百分比仍留在原item，直伤收益不消费这里的评分投影。
        return engine, weights, main_weights, {"item_type": "tape", "area": 15, "quality": quality,
            "main_stats": _property_label(detail, str(first_main_stat.get("property_id") or "")),
            "main_value": None, "sub_stats": dict.fromkeys(sub_stat_names, 0.0)}
    area = int(item.get("grid_count") or 0) or grid_count_from_geometry(
        item.get("geometry")
    )
    return engine, weights, main_weights, {"item_type": "drive", "area": area, "quality": quality,
        "main_stats": None, "main_value": None, "sub_stats": dict.fromkeys(sub_stat_names, 0.0)}


def prefetch_official_role_hidden_equipment_scores(detail, items, *, property_weights, main_property_weights=None, config_dir=None, static_database_path=None):
    rows = [_hidden_equipment_score_inputs(detail, item, property_weights=property_weights,
        main_property_weights=main_property_weights, config_dir=config_dir,
        static_database_path=static_database_path) for item in items]
    if rows:
        engine, weights, mains, _ = rows[0]
        engine.prepare_scores([row[3] for row in rows], profiles=[{"key": "profile", "weights": weights, "main_weights": mains}])


def calculate_official_role_hidden_equipment_score(
    detail: Mapping[str, Any],
    item: Mapping[str, Any],
    *,
    property_weights: Mapping[str, float],
    main_property_weights: Mapping[str, float] | None = None,
    config_dir: str | Path | None = None,
    static_database_path: str | Path | None = None,
) -> float:
    """Score one candidate with the role page's final weights, not direct damage.

    This is intentionally the same category/quality/shape scoring model used
    by result cards.  Role details display this score and its grade; replacement
    choices also use it for ordering. Direct-damage gain is a separate metric.
    """

    engine, weights, mains, raw = _hidden_equipment_score_inputs(detail, item,
        property_weights=property_weights, main_property_weights=main_property_weights,
        config_dir=config_dir, static_database_path=static_database_path)
    engine.prepare_scores([raw], profiles=[{"key": "profile", "weights": weights, "main_weights": mains}])
    maximum = engine.max_theoretical_weight(weights)
    from types import SimpleNamespace
    projected = SimpleNamespace(**raw)
    return (engine.calculate_cartridge_score(projected, weights, maximum, mains) if raw["item_type"] == "tape"
            else engine.calculate_drive_score(projected, weights, maximum))


def calculate_official_role_damage_batch(details, context_key, *, calculator=None):
    if not details:
        return ()
    with role_damage_calculator(details[0], calculator) as owned:
        groups = tuple(_role_panel_damage_inputs(detail, context_key) for detail in details)
        values = owned.totals(groups)
    return tuple(value if group and value > 0 else None for group, value in zip(groups, values, strict=True))
