# 解析账号觉醒多选，并按正式静态效果启用三/六觉共鸣。
"""Resolve selected awakening effects without depending on Qt or SQLite."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from src.services.damage_calculation_service import skill_tier_for_effective_level


_RESONANCE_SUFFIX = re.compile(r"(?:^|_)(\d+)$")


def _normal_effect_ids(awakenings: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    return tuple(
        str(effect.get("effect_id") or "")
        for effect in awakenings
        if str(effect.get("awaken_type") or "") == "Awaken_Effect"
        and str(effect.get("effect_id") or "")
    )


def resolve_selected_awaken_effect_ids(
    profile: Mapping[str, Any],
    awakenings: Sequence[Mapping[str, Any]],
) -> tuple[str, ...]:
    """Resolve explicit selections or preserve legacy numeric profiles by order."""

    normal_ids = _normal_effect_ids(awakenings)
    if bool(profile.get("awakening_selection_initialized")):
        requested = {
            str(effect_id)
            for effect_id in profile.get("selected_awaken_effect_ids") or ()
        }
        return tuple(effect_id for effect_id in normal_ids if effect_id in requested)
    count = max(0, min(int(profile.get("awakening_level") or 0), len(normal_ids)))
    return normal_ids[:count]


def active_awaken_effects(
    profile: Mapping[str, Any],
    awakenings: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], ...]:
    """Return selected normal effects plus count-gated resonance effects."""

    selected_ids = set(resolve_selected_awaken_effect_ids(profile, awakenings))
    selected_count = len(selected_ids)
    active: list[Mapping[str, Any]] = []
    for effect in awakenings:
        effect_id = str(effect.get("effect_id") or "")
        awaken_type = str(effect.get("awaken_type") or "")
        if awaken_type == "Awaken_Effect" and effect_id in selected_ids:
            active.append(effect)
            continue
        if awaken_type != "Awaken_Resonance":
            continue
        match = _RESONANCE_SUFFIX.search(effect_id)
        if match is not None and selected_count >= int(match.group(1)):
            active.append(effect)
    return tuple(active)


def awaken_skill_level_delta(
    profile: Mapping[str, Any],
    awakenings: Sequence[Mapping[str, Any]],
    skill_id: str,
) -> int:
    """Sum formal skill-level modifiers from active awakening effects."""

    target = str(skill_id)
    return sum(
        int(row.get("level_delta") or 0)
        for effect in active_awaken_effects(profile, awakenings)
        for row in effect.get("skill_level_bonuses") or ()
        if str(row.get("skill_id") or "") == target
    )


def resolve_awakening_profile(
    profile: Mapping[str, Any],
    awakenings: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return a profile with an explicit, stable normal-effect selection."""

    selected = resolve_selected_awaken_effect_ids(profile, awakenings)
    return {
        **dict(profile),
        "awakening_level": len(selected),
        "selected_awaken_effect_ids": list(selected),
        "awakening_selection_initialized": True,
    }


def _description_damage_value(
    damage: Mapping[str, Any],
    profile: Mapping[str, Any],
    awakenings: Sequence[Mapping[str, Any]],
) -> float | None:
    ability_id = str(damage.get("ability_id") or "")
    if not ability_id:
        return None
    base_level = int((profile.get("skill_levels") or {}).get(ability_id, 1))
    effective_level = base_level + awaken_skill_level_delta(
        profile,
        awakenings,
        ability_id,
    )
    choices = (
        ("attack", damage.get("atk_rate_base") or ()),
        ("health", damage.get("hp_rate_base") or ()),
        ("defense", damage.get("def_rate_base") or ()),
    )
    _scaling, values = next(
        ((scaling, values) for scaling, values in choices if values),
        ("", ()),
    )
    if not values:
        return None
    tier = min(skill_tier_for_effective_level(effective_level), len(values) - 1)
    value = float(values[tier])
    return value * 100.0


def render_awaken_effect_description(
    effect: Mapping[str, Any],
    profile: Mapping[str, Any],
    awakenings: Sequence[Mapping[str, Any]],
) -> str:
    """Render official numbered placeholders from linked damage tier arrays."""

    description = str(effect.get("description_zh") or "")
    for ordinal, damage in enumerate(effect.get("description_damage_entries") or ()):
        value = _description_damage_value(damage, profile, awakenings)
        if value is None:
            continue
        shown = f"{value:.6f}".rstrip("0").rstrip(".")
        description = description.replace("{" + str(ordinal) + "}", shown)
    return description
