# 验证多个角色选择列表共用首字拼音顺序且不更改正式身份。
"""Behavioral ordering contract for character pickers."""

from src.services.progression.planner_service import (
    CultivationRole,
    _deduplicate_roles,
)


def test_cultivation_picker_deduplicates_before_alphabetic_sort() -> None:
    roles = _deduplicate_roles((
        CultivationRole(1057, "明音凛"),
        CultivationRole(1042, "黑羽"),
        CultivationRole(2042, "黑羽"),
        CultivationRole(1004, "安魂曲"),
    ))

    assert roles == (
        CultivationRole(1004, "安魂曲"),
        CultivationRole(1042, "黑羽"),
        CultivationRole(1057, "明音凛"),
    )
