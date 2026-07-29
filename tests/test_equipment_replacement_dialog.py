# 验证替换比较会同时显示属性收益与损失。
from src.ui.equipment_replacement_dialog import comparison_item_views


def test_comparison_marks_old_and_new_stat_values_by_direction() -> None:
    current, selected = comparison_item_views(
        {
            "main_stats": (),
            "sub_stats": (
                {"property_id": "CritBase", "value": "+5%"},
                {"property_id": "AtkUp", "value": "+8%"},
            ),
        },
        {
            "main_stats": (),
            "sub_stats": (
                {"property_id": "CritBase", "value": "+7%"},
                {"property_id": "AtkUp", "value": "+6%"},
            ),
        },
    )

    old_stats = {item["property_id"]: item for item in current["sub_stats"]}
    new_stats = {item["property_id"]: item for item in selected["sub_stats"]}
    assert old_stats["CritBase"]["comparison_background"] == "#f85149"
    assert new_stats["CritBase"]["comparison_background"] == "#2ea043"
    assert old_stats["AtkUp"]["comparison_background"] == "#2ea043"
    assert new_stats["AtkUp"]["comparison_background"] == "#f85149"
