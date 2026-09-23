# 验证多个角色选择列表共用首字拼音顺序且不更改正式身份。
"""Behavioral ordering contract for character pickers."""

from src.domain.role_name_order import role_name_sort_key
from src.services.cultivation_planner_service import (
    CultivationRole,
    _deduplicate_roles,
)


def test_role_name_sort_key_uses_first_meaningful_character() -> None:
    names = ("早雾", "明音凛", "「零」", "黑羽", "安魂曲")

    assert sorted(names, key=role_name_sort_key) == [
        "安魂曲", "黑羽", "「零」", "明音凛", "早雾",
    ]


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
