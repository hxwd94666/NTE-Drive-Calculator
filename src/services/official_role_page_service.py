# 将官方静态资料与账号指针投影为新角色页面模型。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from src.app import runtime
from src.services.character_weight_service import ensure_account_character_weights
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.services.damage_calculation_service import (
    DamageCalculationService,
    DamageScalingStat,
    DirectDamageInput,
    effective_skill_level,
    skill_tier_for_effective_level,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


DEFAULT_THEORY_PROPERTY_IDS = (
    "DamageUpGeneralBase",
    "CritBase",
    "CritDamageBase",
    "AtkUp",
)

OFFICIAL_ROLE_TAB_ORDER_SCOPE = "official_role_tabs"


@dataclass(frozen=True, slots=True)
class OfficialAttributeSummaryValue:
    key: str
    label: str
    value: float
    percent: bool
    weight_property_ids: tuple[str, ...]

_ELEMENT_DAMAGE_PROPERTY = {
    "CHAOS": "DamageUpChaosBase",
    "COSMOS": "DamageUpCosmosBase",
    "INCANTATION": "DamageUpIncantationBase",
    "LAKSHANA": "DamageUpLakshanaBase",
    "NATURE": "DamageUpNatureBase",
    "PSYCHE": "DamageUpPsycheBase",
    "PSYCHICALLY": "DamageUpPsychicallyBase",
}

# The old role panel's benefit_one values define one comparable marginal roll.
# Keep them here as stable UI semantics; the rebuilt page must not read stats.json at runtime.
_ROLE_PANEL_MARGINAL_UNITS = {
    "CritBase": 0.01,
    "CritDamageBase": 0.02,
    "DamageUpGeneralBase": 0.01,
    "AtkUp": 0.0125,
    "AtkAdd": 8.0,
    "ElementDamage": 0.0125,
}


def _asset_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value)
    runtime_root = getattr(runtime, "ASSET_DIR", None)
    if runtime_root is not None:
        return Path(runtime_root) / "game_ui"
    return Path.cwd() / "assets" / "game_ui"


def _maximum_growth(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "level": 80,
            "breakthrough_stage": 6,
            "hp_base": 0.0,
            "atk_base": 0.0,
            "def_base": 0.0,
        }
    return max(rows, key=lambda row: (int(row["level"]), int(row["breakthrough_stage"])))


def _compatible_forks(character: Mapping[str, Any], forks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    character_id = str(character["character_id"])
    group_type = str(character.get("group_type") or "")
    compatible = [
        fork for fork in forks
        if character_id in {str(value) for value in fork.get("exclusive_character_ids") or []}
        or (
            not fork.get("exclusive_character_ids")
            and str(fork.get("raw_group_type") or "") == group_type
        )
    ]
    return sorted(
        compatible,
        key=lambda fork: (
            character_id not in {str(value) for value in fork.get("exclusive_character_ids") or []},
            str(fork.get("quality") or "") != "ORANGE",
            str(fork.get("name_zh") or fork.get("fork_id") or ""),
        ),
    )


def _default_profile(
    character: Mapping[str, Any],
    growth_rows: list[dict[str, Any]],
    forks: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    ordinal: int,
) -> dict[str, Any]:
    growth = _maximum_growth(growth_rows)
    selected_fork = forks[0] if forks else None
    fork_levels = [
        int(row["level"])
        for row in (selected_fork or {}).get("upgrade_levels", [])
        if row.get("level") is not None
    ]
    selected_skill = next((skill for skill in skills if skill.get("damage_entries")), None)
    skill_levels = {}
    for skill in skills:
        available = [
            int(row["level"])
            for row in skill.get("levels") or []
            if int(row.get("required_breakthrough_stage") or 0) <= int(growth["breakthrough_stage"])
            and int(row.get("required_awaken_level") or 0) <= 6
        ]
        if available:
            skill_levels[str(skill["skill_id"])] = max(available)
    exclusive_ids = {
        str(value) for value in (selected_fork or {}).get("exclusive_character_ids") or []
    }
    return {
        "character_id": int(character["character_id"]),
        "character_level": int(growth["level"]),
        "breakthrough_stage": int(growth["breakthrough_stage"]),
        "awakening_level": 6,
        "fork_id": selected_fork.get("fork_id") if selected_fork else None,
        "fork_level": max(fork_levels) if fork_levels else None,
        "fork_refinement_level": (
            1 if str(character["character_id"]) in exclusive_ids else 5
        ) if selected_fork else None,
        "selected_skill_id": selected_skill.get("skill_id") if selected_skill else None,
        "skill_levels": skill_levels,
        "ordinal": ordinal,
        "is_active": True,
        "persisted": False,
    }


def _theory_properties(weights: Mapping[str, float]) -> tuple[str, ...]:
    positive = [(str(key), float(value)) for key, value in weights.items() if float(value) > 0]
    if not positive:
        return DEFAULT_THEORY_PROPERTY_IDS
    positive.sort(key=lambda row: (-row[1], row[0]))
    selected = [property_id for property_id, _weight in positive[:4]]
    for property_id in DEFAULT_THEORY_PROPERTY_IDS:
        if len(selected) >= 4:
            break
        if property_id not in selected:
            selected.append(property_id)
    return tuple(selected)


def _resolved_plan_items(user_dao: UserDataDao, plan: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not plan or plan.get("source_snapshot_id") is None:
        return []
    items = user_dao.list_inventory_items(int(plan["source_snapshot_id"]))
    by_uid = {(int(item["uid_serial"]), int(item["uid_slot"])): item for item in items}
    resolved = []
    for assignment in plan.get("assignments") or []:
        raw_assignment = dict(assignment.get("raw_assignment") or assignment)
        raw_assignment.update({
            "uid_serial": int(assignment["uid_serial"]),
            "uid_slot": int(assignment["uid_slot"]),
            "kind": assignment["kind"],
            "target_row": assignment.get("target_row"),
            "target_column": assignment.get("target_column"),
        })
        item = (
            virtual_equipment_inventory_item(raw_assignment)
            if is_virtual_equipment_assignment(raw_assignment)
            else by_uid.get(
                (int(assignment["uid_serial"]), int(assignment["uid_slot"]))
            )
        )
        if item is not None:
            row = dict(item)
            row["equipped"] = False
            row["equipped_character_id"] = None
            row["equipped_character_name"] = ""
            row.pop("equipped_character_icon_path", None)
            row["target_row"] = assignment.get("target_row")
            row["target_column"] = assignment.get("target_column")
            resolved.append(row)
    return resolved


def _fork_property_stats(detail: Mapping[str, Any]) -> dict[str, float]:
    profile = detail.get("profile") or {}
    fork_id = profile.get("fork_id")
    level = int(profile.get("fork_level") or 0)
    template = next(
        (fork for fork in detail.get("forks") or [] if fork.get("fork_id") == fork_id),
        None,
    )
    if template is None or level <= 0:
        return {}
    upgrade_rows = list(template.get("upgrade_levels") or ())
    selected_upgrade = min(
        upgrade_rows,
        key=lambda row: abs(int(row.get("level") or 0) - level),
    ) if upgrade_rows else None
    breakthrough_rows = [
        row for row in template.get("breakthroughs") or ()
        if int(row.get("max_fork_level") or 0) <= level
    ]
    selected_breakthrough = max(
        breakthrough_rows,
        key=lambda row: int(row.get("stage") or 0),
    ) if breakthrough_rows else None
    totals: dict[str, float] = {}
    for row in (selected_upgrade, selected_breakthrough):
        for modifier in (row or {}).get("modifiers") or ():
            property_id = str(modifier.get("property_id") or "")
            if property_id:
                totals[property_id] = totals.get(property_id, 0.0) + float(modifier.get("value") or 0.0)
    return totals


def calculate_official_role_attribute_summaries(
    detail: Mapping[str, Any],
    items: Iterable[Any],
) -> dict[str, tuple[OfficialAttributeSummaryValue, ...]]:
    """Return equipment-only and complete character-panel summary rows."""

    attributes = detail.get("attributes") or {}
    property_percent = {
        str(property_id): bool(attribute.get("show_percent"))
        for property_id, attribute in attributes.items()
    }
    shape_bonus = detail.get("shape_bonus") or {}
    equipment_totals = calculate_official_equipment_stats(
        items,
        extra_shape_label=str(shape_bonus.get("shape_label") or ""),
        extra_shape_buffs=tuple(
            (
                str(row.get("property_id") or ""),
                float(row.get("display_value") or 0.0),
            )
            for row in shape_bonus.get("properties") or ()
        ),
        property_percent=property_percent,
    )
    equipment_rows = tuple(
        OfficialAttributeSummaryValue(
            key=total.property_id,
            label=_property_label(detail, total.property_id),
            value=float(total.value),
            percent=bool(total.percent),
            weight_property_ids=(total.property_id,),
        )
        for total in equipment_totals
    )
    combined = _fork_property_stats(detail)
    for total in equipment_totals:
        combined[total.property_id] = (
            combined.get(total.property_id, 0.0) + float(total.value)
        )

    profile = detail.get("profile") or {}
    wanted_growth = (
        int(profile.get("character_level") or 0),
        int(profile.get("breakthrough_stage") or 0),
    )
    growth = next(
        (
            row
            for row in detail.get("growth_rows") or ()
            if (
                int(row.get("level") or 0),
                int(row.get("breakthrough_stage") or 0),
            )
            == wanted_growth
        ),
        {},
    )
    character_rows: list[OfficialAttributeSummaryValue] = []

    def add_panel_total(
        key: str,
        label: str,
        growth_key: str,
        base_id: str,
        up_id: str,
        add_id: str,
    ) -> None:
        base = float(growth.get(growth_key) or 0.0) + combined.get(base_id, 0.0)
        value = base * (1.0 + combined.get(up_id, 0.0)) + combined.get(add_id, 0.0)
        if value:
            character_rows.append(
                OfficialAttributeSummaryValue(
                    key=key,
                    label=label,
                    value=value,
                    percent=False,
                    weight_property_ids=(base_id, up_id, add_id),
                )
            )

    add_panel_total("PanelAtk", "总攻击力", "atk_base", "AtkBase", "AtkUp", "AtkAdd")
    add_panel_total(
        "PanelHP", "总生命值", "hp_base", "HPMaxBase", "HPMaxUp", "HPMaxAdd"
    )
    add_panel_total("PanelDef", "总防御力", "def_base", "DefBase", "DefUp", "DefAdd")
    character_rows.extend(
        (
            OfficialAttributeSummaryValue(
                key="PanelCritRate",
                label="暴击率",
                value=0.05 + combined.get("CritBase", 0.0) + combined.get("CritAdd", 0.0),
                percent=True,
                weight_property_ids=("CritBase", "CritAdd"),
            ),
            OfficialAttributeSummaryValue(
                key="PanelCritDamage",
                label="暴击伤害",
                value=0.50
                + combined.get("CritDamageBase", 0.0)
                + combined.get("CritDamageAdd", 0.0),
                percent=True,
                weight_property_ids=("CritDamageBase", "CritDamageAdd"),
            ),
        )
    )
    consumed = {
        "AtkBase", "AtkUp", "AtkAdd",
        "HPMaxBase", "HPMaxUp", "HPMaxAdd",
        "DefBase", "DefUp", "DefAdd",
        "CritBase", "CritAdd", "CritDamageBase", "CritDamageAdd",
    }
    for property_id, value in combined.items():
        if property_id in consumed or not value:
            continue
        character_rows.append(
            OfficialAttributeSummaryValue(
                key=property_id,
                label=_property_label(detail, property_id),
                value=float(value),
                percent=bool(property_percent.get(property_id, False)),
                weight_property_ids=(property_id,),
            )
        )
    return {
        "equipment": equipment_rows,
        "character": tuple(character_rows),
    }


def _equipment_property_stats(
    detail: Mapping[str, Any], items: list[dict[str, Any]],
) -> dict[str, float]:
    property_percent = {
        str(property_id): bool(attribute.get("show_percent"))
        for property_id, attribute in (detail.get("attributes") or {}).items()
    }
    return {
        row.property_id: row.value
        for row in calculate_official_equipment_stats(
            items,
            property_percent=property_percent,
        )
    }


def _property_stats_by_source(
    detail: Mapping[str, Any], context_key: str,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    fork_stats = _fork_property_stats(detail)
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    equipment_stats = _equipment_property_stats(detail, list(context.get("items") or ()))
    totals = dict(fork_stats)
    for property_id, value in equipment_stats.items():
        totals[property_id] = totals.get(property_id, 0.0) + value
    return fork_stats, equipment_stats, totals


def _element_damage_property(element_type: str) -> str | None:
    suffix = str(element_type or "").rsplit("_", 1)[-1]
    return _ELEMENT_DAMAGE_PROPERTY.get(suffix)


def _damage_inputs(detail: Mapping[str, Any], context_key: str) -> tuple[DirectDamageInput, ...]:
    if context_key == "theory":
        return ()
    profile = detail.get("profile") or {}
    wanted_growth = (
        int(profile.get("character_level") or 0),
        int(profile.get("breakthrough_stage") or 0),
    )
    growth = next(
        (
            row for row in detail.get("growth_rows") or ()
            if (int(row["level"]), int(row["breakthrough_stage"])) == wanted_growth
        ),
        None,
    )
    selected_skill_id = profile.get("selected_skill_id")
    skill = next(
        (row for row in detail.get("skills") or () if row.get("skill_id") == selected_skill_id),
        None,
    )
    if growth is None or skill is None:
        return ()
    base_skill_level = int((profile.get("skill_levels") or {}).get(selected_skill_id, 1))
    tier = skill_tier_for_effective_level(
        effective_skill_level(base_skill_level, int(profile.get("awakening_level") or 0))
    )
    _fork_stats, _equipment_stats, stats = _property_stats_by_source(detail, context_key)
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    damage_increases = tuple(
        stats.get(property_id, 0.0)
        for property_id in ("DamageUpGeneralBase", "DamageUpGeneralAdd", element_property)
        if property_id
    )
    common = {
        "attack_base": float(growth.get("atk_base") or 0.0) + stats.get("AtkBase", 0.0),
        "attack_up": stats.get("AtkUp", 0.0),
        "attack_add": stats.get("AtkAdd", 0.0),
        "health_base": float(growth.get("hp_base") or 0.0) + stats.get("HPMaxBase", 0.0),
        "health_up": stats.get("HPMaxUp", 0.0),
        "health_add": stats.get("HPMaxAdd", 0.0),
        "defense_base": float(growth.get("def_base") or 0.0) + stats.get("DefBase", 0.0),
        "defense_up": stats.get("DefUp", 0.0),
        "defense_add": stats.get("DefAdd", 0.0),
        "character_level": float(profile.get("character_level") or 80),
        "enemy_level": 80.0,
        "crit_rate": 0.05 + stats.get("CritBase", 0.0) + stats.get("CritAdd", 0.0),
        "crit_damage": 0.50 + stats.get("CritDamageBase", 0.0) + stats.get("CritDamageAdd", 0.0),
        "defense_penetration": stats.get("DefIgnore", 0.0),
        "defense_reduction": 0.0,
        "damage_increases": damage_increases,
    }
    inputs = []
    for damage in skill.get("damage_entries") or ():
        arrays = (
            (DamageScalingStat.ATTACK, damage.get("atk_rate_base") or ()),
            (DamageScalingStat.HEALTH, damage.get("hp_rate_base") or ()),
            (DamageScalingStat.DEFENSE, damage.get("def_rate_base") or ()),
        )
        scaling, values = next(((kind, values) for kind, values in arrays if values), (None, ()))
        if scaling is None:
            continue
        index = min(tier, len(values) - 1)
        multiplier = float(values[index])
        coefficient = damage.get("modifier_atk_rate_base_coefficient")
        if scaling is DamageScalingStat.ATTACK and coefficient is not None:
            multiplier *= float(coefficient)
        inputs.append(
            DirectDamageInput(
                skill_multiplier=multiplier,
                scaling_stat=scaling,
                **common,
            )
        )
    return tuple(inputs)


def _role_panel_damage_inputs(
    detail: Mapping[str, Any], context_key: str,
) -> tuple[DirectDamageInput, ...]:
    """Return one representative hit normalized to a 100% skill multiplier."""

    inputs = _damage_inputs(detail, context_key)
    if not inputs:
        return ()
    return (replace(inputs[0], skill_multiplier=1.0),)


def _total_direct_damage(inputs: tuple[DirectDamageInput, ...]) -> float:
    return sum(DamageCalculationService.calculate_direct(item).damage for item in inputs)


def calculate_official_role_equipment_gain(
    detail: Mapping[str, Any], context_key: str,
) -> dict[str, float] | None:
    """Compare the combined core-and-module loadout against the same role without it."""

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    baseline_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: {**context, "items": ()},
        },
    }
    baseline_inputs = _role_panel_damage_inputs(baseline_detail, context_key)
    if not baseline_inputs:
        return None
    damage = _total_direct_damage(inputs)
    baseline_damage = _total_direct_damage(baseline_inputs)
    if baseline_damage <= 0:
        return None
    return {
        "damage": damage,
        "baseline_damage": baseline_damage,
        "gain_percent": (damage / baseline_damage - 1.0) * 100.0,
    }


def _same_inventory_item(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    left_uid = left.get("uid") or {}
    right_uid = right.get("uid") or {}
    if left_uid and right_uid and isinstance(left_uid, Mapping) and isinstance(right_uid, Mapping):
        return (
            int(left_uid.get("serial") or 0), int(left_uid.get("slot") or 0)
        ) == (
            int(right_uid.get("serial") or 0), int(right_uid.get("slot") or 0)
        )
    if all(key in left for key in ("uid_serial", "uid_slot")) and all(
        key in right for key in ("uid_serial", "uid_slot")
    ):
        return (
            int(left.get("uid_serial") or 0), int(left.get("uid_slot") or 0)
        ) == (
            int(right.get("uid_serial") or 0), int(right.get("uid_slot") or 0)
        )
    return left is right


def calculate_official_role_item_gain(
    detail: Mapping[str, Any], context_key: str, item: Mapping[str, Any],
) -> dict[str, float] | None:
    """Measure one core/module by removing it from the same frozen equipment context."""

    inputs = _role_panel_damage_inputs(detail, context_key)
    if not inputs:
        return None
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    remaining = tuple(
        candidate
        for candidate in context.get("items") or ()
        if not _same_inventory_item(candidate, item)
    )
    baseline_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: {**context, "items": remaining},
        },
    }
    baseline_inputs = _role_panel_damage_inputs(baseline_detail, context_key)
    if not baseline_inputs:
        return None
    damage = _total_direct_damage(inputs)
    baseline_damage = _total_direct_damage(baseline_inputs)
    if baseline_damage <= 0:
        return None
    return {
        "damage": damage,
        "baseline_damage": baseline_damage,
        "gain_percent": (damage / baseline_damage - 1.0) * 100.0,
    }


def replacement_candidates_for_official_role(
    detail: Mapping[str, Any], context_key: str, target: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Rank same-slot SQLite inventory replacements by the page's direct damage.

    Replacement is intentionally limited to a saved SQLite loadout: it retains
    the stored grid coordinates and never writes an ad-hoc JSON equipment list.
    A module must keep both its official shape and suit, so applying the result
    cannot silently invalidate the saved board or set constraint.
    """

    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    if context_key != "saved" or not context.get("plan"):
        return []
    raw_items = tuple(context.get("items") or ())
    if not raw_items:
        return []
    target_kind = str(target.get("kind") or "")
    target_virtual = bool(target.get("virtual"))
    target_geometry = str(target.get("geometry") or "")
    target_suit = target.get("suit_id")
    eligible = []
    for candidate in detail.get("replacement_items") or ():
        if _same_inventory_item(candidate, target):
            continue
        if str(candidate.get("kind") or "") != target_kind:
            continue
        if any(_same_inventory_item(candidate, equipped) for equipped in raw_items):
            continue
        if target_kind == "module" and (
            str(candidate.get("geometry") or "") != target_geometry
            or (
                not target_virtual
                and candidate.get("suit_id") != target_suit
            )
        ):
            continue
        if (
            target_kind == "core"
            and not target_virtual
            and candidate.get("suit_id") != target_suit
        ):
            continue
        eligible.append(candidate)
    if not eligible:
        return []

    with StaticGameDataDao() as static_dao:
        projected = project_equipment_items_to_max_level(
            (*raw_items, *eligible),
            static_dao,
        )
    items = tuple(projected[:len(raw_items)])
    projected_candidates = projected[len(raw_items):]
    projected_target = next(
        (item for item in items if _same_inventory_item(item, target)),
        dict(target),
    )
    full_level_context = {**context, "items": items}
    full_level_detail = {
        **detail,
        "equipment_contexts": {
            **(detail.get("equipment_contexts") or {}),
            context_key: full_level_context,
        },
    }
    baseline = calculate_official_role_margins(full_level_detail, context_key)
    baseline_damage = float((baseline or {}).get("damage") or 0.0)
    current_gain = calculate_official_role_item_gain(
        full_level_detail,
        context_key,
        projected_target,
    )
    current_direct_damage_score = (
        float(current_gain["gain_percent"]) if current_gain else None
    )
    ranked: list[dict[str, Any]] = []
    for candidate in projected_candidates:
        replaced = tuple(
            candidate if _same_inventory_item(item, projected_target) else item
            for item in items
        )
        candidate_detail = {
            **full_level_detail,
            "equipment_contexts": {
                **(full_level_detail.get("equipment_contexts") or {}),
                context_key: {**full_level_context, "items": replaced},
            },
        }
        margins = calculate_official_role_margins(candidate_detail, context_key)
        damage = float((margins or {}).get("damage") or 0.0)
        if damage <= 0:
            continue
        candidate_gain = calculate_official_role_item_gain(
            candidate_detail,
            context_key,
            candidate,
        )
        ranked.append({
            "item": dict(candidate),
            "current_item": dict(projected_target),
            "baseline_damage": baseline_damage,
            "damage": damage,
            "current_direct_damage_score": current_direct_damage_score,
            "direct_damage_score": (
                float(candidate_gain["gain_percent"]) if candidate_gain else None
            ),
            "gain_percent": (
                (damage / baseline_damage - 1.0) * 100.0 if baseline_damage > 0 else 0.0
            ),
        })
    return sorted(ranked, key=lambda row: float(row["damage"]), reverse=True)


def save_official_role_replacement(
    user_database_path: str | Path,
    detail: Mapping[str, Any],
    target: Mapping[str, Any],
    replacement: Mapping[str, Any],
    *,
    score: float | None = None,
) -> int:
    """Persist one accepted saved-plan replacement as the next active plan."""

    context = (detail.get("equipment_contexts") or {}).get("saved") or {}
    plan = context.get("plan")
    if not isinstance(plan, Mapping) or plan.get("source_snapshot_id") is None:
        raise ValueError("请先保存一套 SQLite 配装方案，再使用替换优化")
    assignments = []
    replaced = False
    replacement_uid = (
        int(replacement.get("uid_serial") or 0), int(replacement.get("uid_slot") or 0)
    )
    for source in plan.get("assignments") or ():
        assignment = dict(source.get("raw_assignment") or source)
        source_uid = (int(source.get("uid_serial") or 0), int(source.get("uid_slot") or 0))
        target_uid = (int(target.get("uid_serial") or 0), int(target.get("uid_slot") or 0))
        if source_uid == target_uid:
            assignment.pop("virtual", None)
            assignment.pop("virtual_equipment", None)
            assignment.update({
                "uid_serial": replacement_uid[0],
                "uid_slot": replacement_uid[1],
                "kind": str(replacement.get("kind") or ""),
                "geometry": replacement.get("geometry"),
                "grid_count": replacement.get("grid_count"),
            })
            replaced = True
        assignments.append(assignment)
    if not replaced:
        raise ValueError("目标装备不属于当前 SQLite 配装方案")
    if len({(int(row.get("uid_serial") or 0), int(row.get("uid_slot") or 0)) for row in assignments}) != len(assignments):
        raise ValueError("替换装备已在当前方案中使用")
    payload = dict(plan.get("payload") or {})
    payload.update({"source": "official_role_replacement", "replaces_plan_id": plan.get("plan_id")})
    role_name = str((detail.get("character") or {}).get("name_zh") or plan["character_id"])
    with UserDataDao(user_database_path) as user_dao:
        saved_plan_ids = user_dao.replace_active_loadout_plans([{
            "name": f"替换优化：{role_name}",
            "character_id": int(plan["character_id"]),
            "source_snapshot_id": int(plan["source_snapshot_id"]),
            "assignments": assignments,
            "status": (
                "incomplete"
                if any(is_virtual_equipment_assignment(row) for row in assignments)
                else "saved"
            ),
            "score": score,
            "payload": payload,
        }])
    return saved_plan_ids[0]


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
        ("AtkUp", "攻击力%", "attack_up", _ROLE_PANEL_MARGINAL_UNITS["AtkUp"], True),
        ("AtkAdd", "攻击力", "attack_add", _ROLE_PANEL_MARGINAL_UNITS["AtkAdd"], False),
        ("CritBase", "暴击率%", "crit_rate", _ROLE_PANEL_MARGINAL_UNITS["CritBase"], True),
        (
            "CritDamageBase", "暴击伤害%", "crit_damage",
            _ROLE_PANEL_MARGINAL_UNITS["CritDamageBase"], True,
        ),
        (
            "DamageUpGeneralBase", "伤害增加%", "damage_increases",
            _ROLE_PANEL_MARGINAL_UNITS["DamageUpGeneralBase"], True,
        ),
    ]
    if element_property:
        candidates.append((
            element_property, "异能伤害%", "damage_increases",
            _ROLE_PANEL_MARGINAL_UNITS["ElementDamage"], True,
        ))
    configured = {
        str(property_id)
        for property_id in (detail.get("property_weights") or DEFAULT_THEORY_PROPERTY_IDS)
    }
    rows = []
    for property_id, label, field, unit, is_percent in candidates:
        if property_id not in configured:
            continue
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
    rows.sort(key=lambda row: -row["gain_percent"])
    return {
        "damage": base_damage,
        "rows": rows,
        "context_key": context_key,
        "warning": "精炼被保存为官方弧盘指针；其条件被动尚未规范化为静态数值。",
    }


def _property_label(detail: Mapping[str, Any], property_id: str) -> str:
    attribute = (detail.get("attributes") or {}).get(property_id) or {}
    return str(
        attribute.get("display_name_zh")
        or attribute.get("filter_name_zh")
        or property_id
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

    damage_sources = []
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
    for source, source_stats in (("弧盘", fork_stats), ("空幕/驱动", equipment_stats)):
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
                    f"{item['label']} {item['value'] * 100:g}%"
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


def load_official_role_detail(
    user_database_path: str | Path,
    character_id: int,
    *,
    asset_root: str | Path | None = None,
) -> dict[str, Any]:
    """Resolve one page model from static SQLite plus the account SQLite pointers."""

    catalog = GameUiAssetCatalog(_asset_root(asset_root))
    ensure_account_character_weights(user_database_path, (character_id,))
    with StaticGameDataDao() as static_dao, UserDataDao(user_database_path) as user_dao:
        character = static_dao.get_character(character_id)
        if character is None:
            raise ValueError(f"官方角色不存在：{character_id}")
        growth_rows = static_dao.list_character_panel_growth(character_id)
        skills = static_dao.list_character_skills(character_id)
        awakenings = static_dao.list_character_awaken_effects(character_id)
        forks = _compatible_forks(character, static_dao.list_fork_templates())
        saved_profile = user_dao.get_character_profile(character_id)
        profile = dict(saved_profile) if saved_profile else _default_profile(
            character, growth_rows, forks, skills, 0
        )
        profile["persisted"] = saved_profile is not None
        current_items = user_dao.list_current_inventory_items(
            equipped=True, character_id=character_id
        )
        plans = [plan for plan in user_dao.list_loadout_plans(character_id) if plan["is_active"]]
        saved_plan = plans[0] if plans else None
        saved_items = _resolved_plan_items(user_dao, saved_plan)
        replacement_items = (
            user_dao.list_inventory_items(int(saved_plan["source_snapshot_id"]))
            if saved_plan and saved_plan.get("source_snapshot_id") is not None
            else []
        )
        characters = {
            int(row["character_id"]): row
            for row in static_dao.list_characters()
        }
        owner_by_uid: dict[tuple[int, int], int] = {}
        for row in user_dao.list_active_loadout_equipment_owners():
            owner_by_uid.setdefault(
                (int(row["uid_slot"]), int(row["uid_serial"])),
                int(row["character_id"]),
            )
        for item in replacement_items:
            uid = (int(item["uid_slot"]), int(item["uid_serial"]))
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
        shape_bonus = static_dao.get_character_shape_bonus(character_id)
        graduation_template = static_dao.get_character_graduation_template(character_id)
        account_weights = user_dao.get_character_weight_preferences(character_id)
        workshop_weights = static_dao.get_character_recommended_weights(character_id)
        weight_record = account_weights or workshop_weights or {}
        weights = {
            str(key): float(value)
            for key, value in (weight_record.get("property_weights") or {}).items()
        }
        attributes = {
            row["attribute_id"]: row for row in static_dao.list_equipment_attributes()
        }
        equipment_items = static_dao.list_equipment_items()
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
        "forks": forks,
        "equipment_plan": equipment_plan,
        "shape_bonus": shape_bonus,
        "graduation_template": graduation_template,
        "attributes": attributes,
        "item_names": item_names,
        "item_icon_paths": item_icon_paths,
        "property_weights": weights,
        "main_property_weights": {
            str(key): float(value)
            for key, value in (weight_record.get("main_property_weights") or {}).items()
        },
        "property_weight_source": str(weight_record.get("source_kind") or "default"),
        "property_weights_from_account": account_weights is not None,
        "theory_weights": theory_weights,
        "theory_weights_persisted": account_weights is not None,
        "replacement_items": replacement_items,
        "equipment_contexts": {
            "current": {
                "title": "游戏当前",
                "items": current_items,
                "available": bool(current_items),
            },
            "saved": {
                "title": "已保存配装",
                "items": saved_items,
                "plan": saved_plan,
                "available": bool(saved_items),
            },
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
