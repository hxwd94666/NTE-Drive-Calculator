# 验证战报确认环境的中文显示名称和旧配置回退规则。
"""Regression coverage for player-facing battle environment labels."""

from src.domain.battle_report import BattleTargetCondition
from src.services.battle_environment_condition_service import (
    display_battle_environment_name,
)


def _condition(**values) -> BattleTargetCondition:
    return BattleTargetCondition(
        target_name=values.pop("target_name", "测试目标"),
        enemy_level=90.0,
        scene="outer_realm",
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=(),
        **values,
    )


def test_environment_name_prefers_saved_chinese_name() -> None:
    assert display_battle_environment_name(
        _condition(environment_name="材料副本 · 机械工坊 · 难度 3")
    ) == "材料副本 · 机械工坊 · 难度 3"


def test_environment_name_hides_raw_target_path() -> None:
    assert display_battle_environment_name(
        _condition(
            target_name="/Game/Blueprints/Character/Monster/Boss_06/Boss_06_BP",
            environment_kind="open_world",
        )
    ) == "大世界"


def test_environment_name_reads_legacy_clone_and_outer_realm_refs() -> None:
    assert display_battle_environment_name(
        _condition(environment_kind="open_world", environment_ref="clone|a|b|3")
    ) == "材料 / 养成副本"
    assert display_battle_environment_name(
        _condition(
            environment_kind="outer_realm",
            environment_ref="season|6|FirstHalf",
        )
    ) == "轨外之境第6层上半"
    assert display_battle_environment_name(
        _condition(
            environment_kind="outer_realm",
            environment_ref="season|6|mixed",
        )
    ) == "轨外之境第6层上下半"
