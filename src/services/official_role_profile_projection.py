# 统一角色页与分享统计的账号养成、原生观测及发行模板投影。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.native_role_profile_projection import project_native_role_profile
from src.services.official_role_attribute_service import _default_profile
from src.services.official_role_awakening_service import resolve_awakening_profile


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
