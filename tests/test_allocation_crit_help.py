# 测试计算页面的暴击说明。
"""Public copy contract for allocation critical-rate thresholds."""

from src.features.allocation.role_selector_help import CRIT_RATE_CAP_HELP, CRIT_THRESHOLD_HELP
from src.features.weighted_allocation.help_text import WEIGHTED_CRIT_THRESHOLD_HELP


def test_critical_rate_help_names_the_exact_included_and_excluded_sources() -> None:
    expected_sources = "5% 基础 + 空幕词条 + 额外驱动加成"
    minimum_exclusions = "不含弧盘、好感度 10 级、角色成长、武器和其他 Buff"

    assert expected_sources in CRIT_THRESHOLD_HELP
    assert minimum_exclusions in CRIT_THRESHOLD_HELP
    assert "好感暴击率不降低最小值" in CRIT_THRESHOLD_HELP
    assert "未达标时优先补暴击" in CRIT_THRESHOLD_HELP
    assert "超过上限的方案无效" not in CRIT_THRESHOLD_HELP
    assert "100% − 已选弧盘暴击率 − 已启用好感度 10 级暴击率" in CRIT_RATE_CAP_HELP
    assert "手动填写只会进一步收紧" in CRIT_RATE_CAP_HELP
    assert "超过上限的方案无效" in CRIT_RATE_CAP_HELP
    assert "未达标时优先补暴击" not in CRIT_RATE_CAP_HELP
    assert expected_sources in WEIGHTED_CRIT_THRESHOLD_HELP
    assert "不含弧盘、角色成长、武器和其他 Buff" in WEIGHTED_CRIT_THRESHOLD_HELP
