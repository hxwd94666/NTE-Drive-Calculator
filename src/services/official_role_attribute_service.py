# 计算官方角色页面的面板属性、装备收益与直接伤害输入。
"""Official SQLite-only data boundary for the rebuilt character page."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Mapping

from src.domain.official_role import (
    DEFAULT_THEORY_PROPERTY_IDS,
    ELEMENT_DAMAGE_PROPERTY_BY_TYPE,
    OfficialAttributeSummaryValue,
)
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.services.damage_calculation_service import (
    DamageCalculationService,
    DamageScalingStat,
    DirectDamageInput,
    calculate_attribute_value,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.official_role_labels import _property_label
from src.services.world_bonus_settings_service import world_bonus_property_stats


def _asset_root(value: str | Path | None) -> Path:
    if value is not None:
        return Path(value)
    # 兼容调用也只能读取随程序发布的资源，不能受进程当前目录影响。
    return bundled_game_ui_asset_root()


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
        if str(fork.get("raw_group_type") or "") == group_type
    ]

    def recommendation_rank(fork: Mapping[str, Any]) -> int:
        ranks = [
            int(row.get("ordinal") or 0)
            for row in fork.get("cultivation_recommendations") or ()
            if str(row.get("character_id")) == character_id
        ]
        return min(ranks) if ranks else 1_000_000

    return sorted(
        compatible,
        key=lambda fork: (
            recommendation_rank(fork),
            str(fork.get("quality") or "") != "ORANGE",
            str(fork.get("name_zh") or fork.get("fork_id") or ""),
        ),
    )


def _default_profile(
    character: Mapping[str, Any],
    growth_rows: list[dict[str, Any]],
    forks: list[dict[str, Any]],
    skills: list[dict[str, Any]],
    awakenings: list[dict[str, Any]],
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
            skill_levels[str(skill["skill_id"])] = max(available) + 1
    exclusive_ids = {
        str(value) for value in (selected_fork or {}).get("exclusive_character_ids") or []
    }
    return {
        "character_id": int(character["character_id"]),
        "character_level": int(growth["level"]),
        "breakthrough_stage": int(growth["breakthrough_stage"]),
        "awakening_level": 0,
        "selected_awaken_effect_ids": [],
        "awakening_selection_initialized": True,
        "likeability_level_10_enabled": True,
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


def _resolved_plan_items(
    user_dao: UserDataDao,
    plan: Mapping[str, Any] | None,
    *,
    snapshot_items: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve a plan while reusing an already loaded source snapshot.

    The detail model needs both the saved assignments and the replacement pool.
    They are two views of one immutable snapshot, so issuing a second full
    ``list_inventory_items`` query only adds UI latency and can obscure the
    snapshot boundary during a concurrent sync.
    """

    if not plan or plan.get("source_snapshot_id") is None:
        return []
    items = (
        [dict(item) for item in snapshot_items]
        if snapshot_items is not None
        else user_dao.list_inventory_items(int(plan["source_snapshot_id"]))
    )
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


def _likeability_property_stats(detail: Mapping[str, Any]) -> dict[str, float]:
    profile = detail.get("profile") or {}
    if not bool(profile.get("likeability_level_10_enabled")):
        return {}
    return {
        str(row.get("property_id") or ""): float(row.get("value") or 0.0)
        for row in (detail.get("likeability_bonus") or {}).get("properties") or ()
        if str(row.get("property_id") or "")
    }


def _world_bonus_property_stats(detail: Mapping[str, Any]) -> dict[str, float]:
    return world_bonus_property_stats(detail.get("world_bonus"))


def _add_property_stats(
    totals: dict[str, float], values: Mapping[str, float],
) -> None:
    for property_id, value in values.items():
        totals[property_id] = totals.get(property_id, 0.0) + float(value)


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
    extra_shape_label, extra_shape_buffs = _shape_bonus_parameters(detail)
    equipment_totals = calculate_official_equipment_stats(
        items,
        extra_shape_label=extra_shape_label,
        extra_shape_buffs=extra_shape_buffs,
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
    _add_property_stats(combined, _likeability_property_stats(detail))
    _add_property_stats(combined, _world_bonus_property_stats(detail))
    for total in equipment_totals:
        combined[total.property_id] = (
            combined.get(total.property_id, 0.0) + float(total.value)
        )

    profile = detail.get("profile") or {}
    wanted_growth = (
        int(profile.get("character_level") or 0),
        int(profile.get("breakthrough_stage") or 0),
    )
    growth: Mapping[str, Any] = next(
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


def _shape_bonus_parameters(
    detail: Mapping[str, Any],
) -> tuple[str, tuple[tuple[str, float], ...]]:
    """Normalize the account-resolved extra-shape bonus for every panel path."""

    shape_bonus = detail.get("shape_bonus") or {}
    return (
        str(shape_bonus.get("shape_label") or ""),
        tuple(
            (
                str(row.get("property_id") or ""),
                float(row.get("display_value") or 0.0),
            )
            for row in shape_bonus.get("properties") or ()
            if str(row.get("property_id") or "")
        ),
    )


def _item_uid(item: Mapping[str, Any]) -> tuple[int, int] | None:
    """Return a native inventory UID in its stable ``(slot, serial)`` order."""

    raw_uid = item.get("uid") or {}
    if isinstance(raw_uid, Mapping) and raw_uid:
        return int(raw_uid.get("slot") or 0), int(raw_uid.get("serial") or 0)
    if "uid_slot" in item or "uid_serial" in item:
        return int(item.get("uid_slot") or 0), int(item.get("uid_serial") or 0)
    return None


def _context_calculation_items(context: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Use a matching full-level projection without reading a stale replacement list.

    A replacement context changes ``items`` first.  Its max-level projection
    must change in the same operation; otherwise the direct-damage calculation
    silently keeps scoring the previous equipment set.  When the two UID sets
    differ, the caller's current items are the only coherent calculation input.
    """

    if "calculation_items" in context:
        calculation_items = list(context.get("calculation_items") or ())
        items = list(context.get("items") or ())
        calculation_uids = {
            _item_uid(item)
            for item in calculation_items
            if _item_uid(item) is not None
        }
        item_uids = {
            _item_uid(item)
            for item in items
            if _item_uid(item) is not None
        }
        if calculation_uids == item_uids:
            return calculation_items
        return items
    return list(context.get("items") or ())


def _equipment_property_stats(
    detail: Mapping[str, Any], items: list[dict[str, Any]], *, include_shape_bonus: bool = True,
) -> dict[str, float]:
    property_percent = {
        str(property_id): bool(attribute.get("show_percent"))
        for property_id, attribute in (detail.get("attributes") or {}).items()
    }
    extra_shape_label, extra_shape_buffs = _shape_bonus_parameters(detail)
    return {
        row.property_id: row.value
        for row in calculate_official_equipment_stats(
            items,
            extra_shape_label=extra_shape_label if include_shape_bonus else "",
            extra_shape_buffs=extra_shape_buffs if include_shape_bonus else (),
            property_percent=property_percent,
        )
    }


def _property_stats_by_source(
    detail: Mapping[str, Any], context_key: str,
) -> tuple[dict[str, float], dict[str, float], dict[str, float]]:
    fork_stats = _fork_property_stats(detail)
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    equipment_stats = _equipment_property_stats(
        detail, _context_calculation_items(context),
    )
    totals = dict(fork_stats)
    _add_property_stats(totals, _likeability_property_stats(detail))
    _add_property_stats(totals, _world_bonus_property_stats(detail))
    _add_property_stats(totals, equipment_stats)
    return fork_stats, equipment_stats, totals


def calculate_official_role_combat_stat_sources(
    detail: Mapping[str, Any],
    items: Iterable[Any],
) -> dict[str, tuple[OfficialAttributeSummaryValue, ...]]:
    """Freeze formula inputs by owner before producing resolved panel values."""

    profile = detail.get("profile") or {}
    wanted_growth = (
        int(profile.get("character_level") or 0),
        int(profile.get("breakthrough_stage") or 0),
    )
    growth: Mapping[str, Any] = next(
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
    property_percent = {
        str(property_id): bool(attribute.get("show_percent"))
        for property_id, attribute in (detail.get("attributes") or {}).items()
    }

    def rows(
        values: Mapping[str, float], *, include_zero: bool = False,
    ) -> tuple[OfficialAttributeSummaryValue, ...]:
        return tuple(
            OfficialAttributeSummaryValue(
                key=property_id,
                label=_property_label(detail, property_id),
                value=float(value),
                percent=property_percent.get(
                    property_id,
                    property_id.endswith("Up")
                    or "Crit" in property_id
                    or "Damage" in property_id
                    or property_id.startswith("DefIgnore"),
                ),
                weight_property_ids=(property_id,),
            )
            for property_id, value in sorted(values.items())
            if property_id and (include_zero or float(value) != 0.0)
        )

    character = {
        "AtkBase": float(growth.get("atk_base") or 0.0),
        "HPMaxBase": float(growth.get("hp_base") or 0.0),
        "DefBase": float(growth.get("def_base") or 0.0),
        "CritBase": 0.05,
        "CritDamageBase": 0.50,
    }
    return {
        "character": rows(character),
        "fork": rows(_fork_property_stats(detail)),
        "likeability": rows(_likeability_property_stats(detail)),
        "world_bonus": rows(
            _world_bonus_property_stats(detail),
            include_zero=True,
        ),
        "equipment": rows(_equipment_property_stats(detail, list(items))),
    }


def calculate_official_role_combat_stat_components(
    detail: Mapping[str, Any],
    items: Iterable[Any],
) -> tuple[OfficialAttributeSummaryValue, ...]:
    """Resolve reusable formula inputs from one frozen role build.

    The values deliberately preserve base/percentage/flat components instead
    of only returning panel totals. Battle-report counterfactual analysis and
    the role page therefore share the same growth, fork, likeability and
    equipment semantics.
    """

    profile = detail.get("profile") or {}
    wanted_growth = (
        int(profile.get("character_level") or 0),
        int(profile.get("breakthrough_stage") or 0),
    )
    growth: Mapping[str, Any] = next(
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
    property_percent = {
        str(property_id): bool(attribute.get("show_percent"))
        for property_id, attribute in (detail.get("attributes") or {}).items()
    }
    totals = _fork_property_stats(detail)
    _add_property_stats(totals, _likeability_property_stats(detail))
    _add_property_stats(totals, _world_bonus_property_stats(detail))
    _add_property_stats(totals, _equipment_property_stats(detail, list(items)))

    rows: list[OfficialAttributeSummaryValue] = []

    def add(
        property_id: str,
        label: str,
        value: float,
        *,
        percent: bool = False,
    ) -> None:
        rows.append(
            OfficialAttributeSummaryValue(
                key=property_id,
                label=label,
                value=float(value),
                percent=percent,
                weight_property_ids=(property_id,),
            )
        )

    add("AtkBase", "基础攻击力", float(growth.get("atk_base") or 0.0) + totals.get("AtkBase", 0.0))
    add("AtkUp", "攻击力提升", totals.get("AtkUp", 0.0), percent=True)
    add("AtkAdd", "固定攻击力", totals.get("AtkAdd", 0.0))
    add(
        "PanelAtk",
        "总攻击力",
        calculate_attribute_value(
            float(growth.get("atk_base") or 0.0) + totals.get("AtkBase", 0.0),
            totals.get("AtkUp", 0.0),
            totals.get("AtkAdd", 0.0),
        ),
    )
    add("HPMaxBase", "基础生命值", float(growth.get("hp_base") or 0.0) + totals.get("HPMaxBase", 0.0))
    add("HPMaxUp", "生命值提升", totals.get("HPMaxUp", 0.0), percent=True)
    add("HPMaxAdd", "固定生命值", totals.get("HPMaxAdd", 0.0))
    add(
        "PanelHP",
        "总生命值",
        calculate_attribute_value(
            float(growth.get("hp_base") or 0.0) + totals.get("HPMaxBase", 0.0),
            totals.get("HPMaxUp", 0.0),
            totals.get("HPMaxAdd", 0.0),
        ),
    )
    add("DefBase", "基础防御力", float(growth.get("def_base") or 0.0) + totals.get("DefBase", 0.0))
    add("DefUp", "防御力提升", totals.get("DefUp", 0.0), percent=True)
    add("DefAdd", "固定防御力", totals.get("DefAdd", 0.0))
    add(
        "PanelDef",
        "总防御力",
        calculate_attribute_value(
            float(growth.get("def_base") or 0.0) + totals.get("DefBase", 0.0),
            totals.get("DefUp", 0.0),
            totals.get("DefAdd", 0.0),
        ),
    )
    add("CritBase", "暴击率", 0.05 + totals.get("CritBase", 0.0) + totals.get("CritAdd", 0.0), percent=True)
    add("CritDamageBase", "暴击伤害", 0.50 + totals.get("CritDamageBase", 0.0) + totals.get("CritDamageAdd", 0.0), percent=True)
    add(
        "DamageUpGeneralBase",
        "通用伤害增强",
        totals.get("DamageUpGeneralBase", 0.0) + totals.get("DamageUpGeneralAdd", 0.0),
        percent=True,
    )
    add("DefIgnore", "防御忽略", totals.get("DefIgnore", 0.0), percent=True)
    add("MagBase", "环合强度", totals.get("MagBase", 0.0))
    add("UnbalIntensityBase", "倾陷强度", totals.get("UnbalIntensityBase", 0.0))
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    if element_property:
        add(
            element_property,
            _property_label(detail, element_property),
            totals.get(element_property, 0.0),
            percent=property_percent.get(element_property, True),
        )
    return tuple(rows)


def _element_damage_property(element_type: str) -> str | None:
    suffix = str(element_type or "").rsplit("_", 1)[-1]
    return ELEMENT_DAMAGE_PROPERTY_BY_TYPE.get(suffix)


def _role_panel_damage_inputs(
    detail: Mapping[str, Any], context_key: str,
) -> tuple[DirectDamageInput, ...]:
    """Return one 100% attack hit using the role's own element and panel."""

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
    if growth is None:
        return ()
    _fork_stats, _equipment_stats, stats = _property_stats_by_source(detail, context_key)
    element_property = _element_damage_property(
        str((detail.get("character") or {}).get("element_type") or "")
    )
    damage_increases = tuple(
        stats.get(property_id, 0.0)
        for property_id in ("DamageUpGeneralBase", "DamageUpGeneralAdd", element_property)
        if property_id
    )
    return (
        DirectDamageInput(
            skill_multiplier=1.0,
            scaling_stat=DamageScalingStat.ATTACK,
            attack_base=float(growth.get("atk_base") or 0.0) + stats.get("AtkBase", 0.0),
            attack_up=stats.get("AtkUp", 0.0),
            attack_add=stats.get("AtkAdd", 0.0),
            health_base=float(growth.get("hp_base") or 0.0) + stats.get("HPMaxBase", 0.0),
            health_up=stats.get("HPMaxUp", 0.0),
            health_add=stats.get("HPMaxAdd", 0.0),
            defense_base=float(growth.get("def_base") or 0.0) + stats.get("DefBase", 0.0),
            defense_up=stats.get("DefUp", 0.0),
            defense_add=stats.get("DefAdd", 0.0),
            character_level=float(profile.get("character_level") or 80),
            enemy_level=80.0,
            crit_rate=0.05 + stats.get("CritBase", 0.0) + stats.get("CritAdd", 0.0),
            crit_damage=0.50 + stats.get("CritDamageBase", 0.0) + stats.get("CritDamageAdd", 0.0),
            defense_penetration=stats.get("DefIgnore", 0.0),
            defense_reduction=0.0,
            damage_increases=damage_increases,
        ),
    )


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
            context_key: {**context, "items": (), "calculation_items": ()},
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
