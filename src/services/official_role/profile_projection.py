# 汇总默认养成、账号显式档案与已确认原生字段的统一投影。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain.official_role.advancement import select_fork_breakthrough
from src.services.official_role.native_profiles import project_native_role_profile
from src.services.official_role.awakening import resolve_awakening_profile


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
    maximum_fork_level = max(fork_levels) if fork_levels else None
    default_fork_breakthrough = select_fork_breakthrough(
        (selected_fork or {}).get("breakthroughs") or (),
        maximum_fork_level or 0,
    )
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
        "fork_level": maximum_fork_level,
        "fork_breakthrough_stage": (
            int(default_fork_breakthrough["stage"])
            if default_fork_breakthrough is not None
            else None
        ),
        "fork_refinement_level": (
            1 if str(character["character_id"]) in exclusive_ids else 5
        ) if selected_fork else None,
        "selected_skill_id": selected_skill.get("skill_id") if selected_skill else None,
        "skill_levels": skill_levels,
        "ordinal": ordinal,
        "is_active": True,
        "persisted": False,
    }


def resolve_official_role_profile(
    character: Mapping[str, Any], growth_rows: list[dict], forks: list[dict],
    skills: list[dict], awakenings: list[dict], *,
    saved_profile: Mapping[str, Any] | None, observation: Mapping[str, Any] | None,
    likeability_bonus: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Use the same local inputs in every mode; rendering never initiates acquisition."""
    profile = dict(saved_profile) if saved_profile else _default_profile(
        character, growth_rows, forks, skills, awakenings, 0,
    )
    if saved_profile is None:
        profile['likeability_level_10_enabled'] = likeability_bonus is not None
    profile = project_native_role_profile(profile, observation, persisted=saved_profile is not None)
    profile = resolve_awakening_profile(profile, awakenings)
    profile['persisted'] = saved_profile is not None
    return profile
