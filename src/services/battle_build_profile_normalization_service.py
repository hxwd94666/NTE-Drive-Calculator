# 毕业模板只提供兜底配装，不构成用户已选择觉醒的证据。
"""Normalize non-observed battle build defaults before freezing or replaying."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.services.advancement_stage_service import legacy_fork_breakthrough_stage


_GRADUATION_SOURCE = "official_graduation"


def normalize_inferred_battle_profile(
    profile: Mapping[str, Any],
) -> dict[str, Any]:
    """Make inferred graduation profiles obey the role-page zero-awaken default."""

    normalized = dict(profile)
    if str(normalized.get("profile_source") or "") != _GRADUATION_SOURCE:
        return normalized
    sources = normalized.get("field_sources") or {}
    defaults = {
        "awakening_level": 0,
        "selected_awaken_effect_ids": [],
        "awakening_selection_initialized": True,
    }
    # 模板是配置的底板；逐字段原生观测才是该字段的实际来源。
    for field, default in defaults.items():
        if sources.get(field) != "native_observed":
            normalized[field] = default
    if (
        normalized.get("fork_id")
        and normalized.get("fork_level") is not None
        and normalized.get("fork_breakthrough_stage") is None
    ):
        normalized["fork_breakthrough_stage"] = legacy_fork_breakthrough_stage(
            int(normalized["fork_level"])
        )
    return normalized


def normalize_inferred_battle_build(
    build: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Repair historical template-derived snapshots without changing saved data."""

    if build is None:
        return None
    normalized_build = dict(build)
    characters = []
    for source_character in build.get("characters") or ():
        character = dict(source_character)
        if str(character.get("profile_source") or "") == _GRADUATION_SOURCE:
            profile = dict(character.get("profile") or {})
            profile["profile_source"] = _GRADUATION_SOURCE
            profile = normalize_inferred_battle_profile(profile)
            character.update({
                "awakening_level": profile["awakening_level"],
                "selected_awaken_effect_ids": tuple(profile["selected_awaken_effect_ids"]),
                "awakening_selection_initialized": profile["awakening_selection_initialized"],
                "profile": profile,
            })
        characters.append(character)
    normalized_build["characters"] = characters
    return normalized_build
