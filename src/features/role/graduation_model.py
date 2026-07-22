# 计算角色直伤毕业率的理论满分基准。
"""Theoretical direct-damage graduation benchmark for a role.

The benchmark deliberately uses a full 20-cell drive board instead of the
player's equipped layout.  Drive sub-stat values and intrinsic attack scale
linearly with area, so the layout itself cannot change the benchmark.  The
only layout-dependent part is the role's extra-shape effect; its count is
resolved once from the same fixed blueprint solver used by allocation.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.solver.orchestrator import NTEPipelineOrchestrator
from src.services.role_fork_template_service import (
    fork_templates_as_weapon_models,
    load_official_role_fork_templates,
)

from .damage_model import calc_direct_damage


FULL_DRIVE_AREA = 20
DRIVE_BASE_ATTACK_PER_AREA = 21.0


@dataclass(frozen=True)
class GraduationBenchmark:
    """The fixed theoretical reference used to turn direct damage into a rate."""

    damage: float
    weapon_name: str
    tape_main_stat: str
    drive_sub_stats: tuple[str, ...]
    tape_sub_stats: tuple[str, ...]
    extra_shape_count: int


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _highest_level_stats(role_model: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    level_stats = role_model.get("level_sub_stats", {}) if isinstance(role_model, dict) else {}
    levels: list[int] = []
    for level in level_stats if isinstance(level_stats, dict) else ():
        try:
            levels.append(int(level))
        except (TypeError, ValueError):
            continue
    if not levels:
        return None, {}
    level = max(levels)
    stats = level_stats.get(str(level), {})
    return level, dict(stats) if isinstance(stats, dict) else {}


def _weight_for_stat(stat: str, weights: dict[str, Any], aliases: dict[str, str]) -> float:
    """Resolve a role weight with the same alias tolerance as equipment scoring."""
    raw = str(stat or "").strip()
    if not raw:
        return 0.0
    names = [raw, aliases.get(raw, raw)]
    if raw.endswith("%"):
        names.append(raw[:-1])
    else:
        names.append(f"{raw}%")
    for name in dict.fromkeys(name for name in names if name):
        value = _as_float(weights.get(name, 0.0))
        if value > 0:
            return value
    targets = set(names)
    for name, value in weights.items():
        if name in targets or aliases.get(str(name).strip(), str(name).strip()) in targets:
            numeric = _as_float(value)
            if numeric > 0:
                return numeric
    return 0.0


def _top_weighted_stats(
    values: dict[str, Any],
    weights: dict[str, Any],
    aliases: dict[str, str],
    count: int = 4,
) -> tuple[str, ...]:
    candidates = [
        (stat, _weight_for_stat(stat, weights, aliases))
        for stat in values
        if _weight_for_stat(stat, weights, aliases) > 0
    ]
    candidates.sort(key=lambda item: (-item[1], item[0]))
    return tuple(stat for stat, _weight in candidates[:count])


def _add_stats(target: dict[str, float], stats: dict[str, Any]) -> None:
    for stat, value in (stats or {}).items():
        numeric = _as_float(value)
        if numeric:
            target[stat] = target.get(stat, 0.0) + numeric


def _canonical_stat(stat: str, stats_config: dict[str, Any]) -> str:
    benefit_aliases = stats_config.get("benefit_alias_mapping", {}) or {}
    stat_aliases = stats_config.get("stat_alias_mapping", {}) or {}
    return benefit_aliases.get(stat, stat_aliases.get(stat, stat))


def _direct_stats_for_benchmark(role_data: dict[str, Any], stats_config: dict[str, Any]) -> dict[str, float]:
    """Mirror the existing role direct-damage sources without runtime file I/O."""
    totals: dict[str, float] = {}

    def add(stat: str, value: Any) -> None:
        numeric = _as_float(value)
        key = _canonical_stat(str(stat or ""), stats_config)
        if key and numeric:
            totals[key] = totals.get(key, 0.0) + numeric

    def add_map(stats: dict[str, Any]) -> None:
        for stat, value in (stats or {}).items():
            add(stat, value)

    add_map(role_data.get("sub_stats", {}))
    weapon = role_data.get("weapon", {}) or {}
    add_map(weapon.get("sub_stats", {}))
    for effect in weapon.get("skill", []) or []:
        if isinstance(effect, dict) and effect.get("key"):
            add(
                effect["key"],
                _as_float(effect.get("value")) * _as_float(effect.get("cover", 0.8)) * _as_float(effect.get("num", 1)),
            )
    tape = role_data.get("tape", {}) or {}
    add_map(tape.get("main_stats", {}))
    add_map(tape.get("sub_stats", {}))
    set_bonus = role_data.get("set_bonus", {}) or {}
    add_map(set_bonus.get("skill", {}))
    coverage = _as_float(set_bonus.get("skill_cover", 0.0))
    for stat, value in (set_bonus.get("skill_2", {}) or {}).items():
        add(stat, _as_float(value) * coverage)
    return totals


def _signature_weapon(role_model: dict[str, Any], weapons_db: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    configured = role_model.get("weapon", {}) if isinstance(role_model, dict) else {}
    name = str(configured.get("name", "") or "") if isinstance(configured, dict) else ""
    weapon_info = weapons_db.get(name, {}) if name else {}
    if not isinstance(weapon_info, dict):
        weapon_info = {}

    level_stats = weapon_info.get("level_sub_stats", {})
    levels = []
    for level in level_stats if isinstance(level_stats, dict) else ():
        try:
            levels.append(int(level))
        except (TypeError, ValueError):
            continue
    highest_level = max(levels) if levels else None
    sub_stats = (
        dict(level_stats.get(str(highest_level), {}))
        if highest_level is not None and isinstance(level_stats.get(str(highest_level), {}), dict)
        else dict(weapon_info.get("sub_stats", {}) or configured.get("sub_stats", {}) or {})
    )
    mix_data = weapon_info.get("mix_level_sub_stats", {})
    mix_one = mix_data.get("1", {}) if isinstance(mix_data, dict) else {}
    skill = mix_one.get("skill", []) if isinstance(mix_one, dict) else []
    if not isinstance(skill, list):
        skill = []
    return name, {"name": name, "level": highest_level or 80, "mix_level": 1, "sub_stats": sub_stats, "skill": deepcopy(skill)}


@lru_cache(maxsize=128)
def graduation_extra_shape_count(config_dir: str, role_name: str) -> int:
    """Read the fixed optimal extra-shape count from the allocation blueprint solver."""
    try:
        orchestrator = NTEPipelineOrchestrator(config_dir)
        role_config = orchestrator.roles_db.get(role_name, {}) or {}
        target_label = str(role_config.get("extra_shape_label", "") or "")
        if not target_label:
            return 0
        blueprints = orchestrator.solve_blueprints([role_name]).get(role_name, [])
        if not blueprints:
            return 0
        blueprint = blueprints[0]
        pieces = list(blueprint.get("set_pieces", []) or []) + list(blueprint.get("extra_pieces", []) or [])
        return sum(
            1
            for shape_id in pieces
            if getattr(orchestrator.shapes_db.get(shape_id), "label", "") == target_label
        )
    except (KeyError, OSError, ValueError):
        return 0


def calculate_graduation_benchmark(
    role_name: str,
    role_data: dict[str, Any],
    *,
    role_model: dict[str, Any],
    role_config: dict[str, Any],
    weapons_db: dict[str, Any],
    stats_config: dict[str, Any],
    extra_shape_count: int,
) -> GraduationBenchmark | None:
    """Build the role's all-gold, full-board direct-damage reference.

    Role weights select the ideal drive and tape sub-stat combination.  Every
    valid, positively weighted tape main stat is then evaluated through the
    actual direct-damage formula, so crit caps and the current stat balance are
    respected without an expensive search.
    """
    if not isinstance(role_model, dict) or not isinstance(role_config, dict):
        return None
    aliases = dict(stats_config.get("stat_alias_mapping", {}) or {})
    weights = dict(role_data.get("weights", {}) or role_config.get("weights", {}) or {})
    gold_values = dict(stats_config.get("gold_base_values", {}) or {})
    tape_sub_values = dict(stats_config.get("tape_stat_values", {}) or {})
    tape_main_values = dict(stats_config.get("tape_main_stat_values", {}) or {})

    drive_sub_stats = _top_weighted_stats(gold_values, weights, aliases)
    tape_sub_stats = _top_weighted_stats(tape_sub_values, weights, aliases)
    # Some support templates intentionally keep only their non-damage weights
    # in my_roles_model.  Their role configuration still supplies a complete
    # direct-damage reference, which lets every role retain a benchmark.
    if len(drive_sub_stats) < 4 or len(tape_sub_stats) < 4:
        weights = dict(role_config.get("weights", {}) or {})
        drive_sub_stats = _top_weighted_stats(gold_values, weights, aliases)
        tape_sub_stats = _top_weighted_stats(tape_sub_values, weights, aliases)
    if len(drive_sub_stats) < 4 or len(tape_sub_stats) < 4:
        return None

    role_level, max_level_stats = _highest_level_stats(role_model)
    if role_level is None:
        return None
    weapon_name, weapon = _signature_weapon(role_model, weapons_db)
    if not weapon_name:
        return None

    base_role = deepcopy(role_model)
    base_stats = dict(base_role.get("sub_stats", {}) or {})
    base_stats.update(max_level_stats)
    ideal_drive_stats: dict[str, float] = {"攻击力": DRIVE_BASE_ATTACK_PER_AREA * FULL_DRIVE_AREA}
    for stat in drive_sub_stats:
        ideal_drive_stats[stat] = _as_float(gold_values[stat]) * FULL_DRIVE_AREA
    for stat, value in (role_config.get("extra_shape_buffs", {}) or {}).items():
        ideal_drive_stats[stat] = ideal_drive_stats.get(stat, 0.0) + _as_float(value) * max(0, extra_shape_count)
    _add_stats(base_stats, ideal_drive_stats)

    base_role["role_name"] = role_name
    base_role["level"] = role_level
    base_role["sub_stats"] = base_stats
    base_role["weapon"] = weapon
    base_role["drive"] = {"drives": []}

    ideal_tape_sub_stats = {stat: _as_float(tape_sub_values[stat]) for stat in tape_sub_stats}
    main_weights = dict(role_config.get("main_weights", {}) or weights)
    candidates = [
        stat
        for stat in tape_main_values
        if _weight_for_stat(stat, main_weights, aliases) > 0
    ]
    if not candidates:
        return None

    best_stat = ""
    best_damage = 0.0
    for main_stat in candidates:
        candidate_role = deepcopy(base_role)
        candidate_role["tape"] = {
            "set_name": role_config.get("default_set", ""),
            "quality": "Gold",
            "main_stats": {main_stat: _as_float(tape_main_values[main_stat])},
            "sub_stats": dict(ideal_tape_sub_stats),
        }
        damage = calc_direct_damage(_direct_stats_for_benchmark(candidate_role, stats_config))
        if damage > best_damage:
            best_damage = damage
            best_stat = main_stat

    if best_damage <= 0 or not best_stat:
        return None
    return GraduationBenchmark(
        damage=best_damage,
        weapon_name=weapon_name,
        tape_main_stat=best_stat,
        drive_sub_stats=drive_sub_stats,
        tape_sub_stats=tape_sub_stats,
        extra_shape_count=max(0, extra_shape_count),
    )


def calculate_graduation_benchmark_from_config(role_name: str, role_data: dict[str, Any], config_dir: str | Path) -> GraduationBenchmark | None:
    """Load immutable config inputs and calculate a benchmark for the role page."""
    config_path = Path(config_dir)
    try:
        import json

        with (config_path / "my_roles_model.json").open("r", encoding="utf-8") as handle:
            role_models = json.load(handle)
        with (config_path / "roles.json").open("r", encoding="utf-8") as handle:
            roles_db = json.load(handle)
        with (config_path / "stats.json").open("r", encoding="utf-8") as handle:
            stats_config = json.load(handle)
    except (OSError, ValueError):
        return None
    return calculate_graduation_benchmark(
        role_name,
        role_data,
        role_model=(role_models or {}).get(role_name, {}),
        role_config=(roles_db or {}).get(role_name, {}),
        weapons_db=fork_templates_as_weapon_models(
            load_official_role_fork_templates()
        ),
        stats_config=stats_config or {},
        extra_shape_count=graduation_extra_shape_count(str(config_path), role_name),
    )
