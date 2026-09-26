# 验证战报角色配置来源归一化不会把理论毕业条件冒充用户实际养成。
from __future__ import annotations

from copy import deepcopy

from src.services.battle_build_profile_normalization_service import (
    normalize_inferred_battle_build,
    normalize_inferred_battle_profile,
)


def test_graduation_profile_does_not_claim_full_awakening() -> None:
    profile = normalize_inferred_battle_profile({
        "profile_source": "official_graduation",
        "awakening_level": 6,
        "selected_awaken_effect_ids": [f"Effect{index}" for index in range(1, 7)],
        "awakening_selection_initialized": True,
    })

    assert profile["awakening_level"] == 0
    assert profile["selected_awaken_effect_ids"] == []
    assert profile["awakening_selection_initialized"] is True


def test_account_role_page_profile_keeps_explicit_awakenings() -> None:
    source = {
        "profile_source": "account_role_page",
        "awakening_level": 3,
        "selected_awaken_effect_ids": ["Effect1", "Effect3", "Effect5"],
    }

    assert normalize_inferred_battle_profile(source) == source


def test_historical_graduation_build_is_repaired_for_replay() -> None:
    build = normalize_inferred_battle_build({
        "characters": [{
            "character_id": 1052,
            "profile_source": "official_graduation",
            "awakening_level": 6,
            "profile": {
                "profile_source": "official_graduation",
                "awakening_level": 6,
                "selected_awaken_effect_ids": ["Effect1", "Effect5"],
            },
        }],
    })

    assert build is not None
    character = build["characters"][0]
    assert character["awakening_level"] == 0
    assert character["profile"]["selected_awaken_effect_ids"] == []


def test_native_awakenings_on_template_survive_replay_normalization() -> None:
    for effects in (["Effect1", "Effect3", "Effect5"], []):
        profile = {
            "profile_source": "official_graduation",
            "awakening_level": len(effects),
            "selected_awaken_effect_ids": effects,
            "awakening_selection_initialized": True,
            "field_sources": {key: "native_observed" for key in (
                "awakening_level", "selected_awaken_effect_ids",
                "awakening_selection_initialized",
            )},
        }
        source = {"characters": [{"character_id": 1036, **profile, "profile": profile}]}
        frozen = deepcopy(source)
        assert normalize_inferred_battle_profile(profile) == profile
        result = normalize_inferred_battle_build(source)
        assert result is not None
        role = result["characters"][0]
        assert role["awakening_level"] == len(effects)
        assert role["selected_awaken_effect_ids"] == tuple(effects)
        assert role["profile"] == profile
        assert source == frozen


def test_native_level_does_not_certify_template_effect_selection() -> None:
    profile = normalize_inferred_battle_profile({
        "profile_source": "official_graduation",
        "awakening_level": 3,
        "selected_awaken_effect_ids": ["Effect1", "Effect2", "Effect3"],
        "field_sources": {"awakening_level": "native_observed"},
    })
    assert profile["awakening_level"] == 3
    assert profile["selected_awaken_effect_ids"] == []
    assert profile["awakening_selection_initialized"] is True
