# 测试计算页面的暴击说明。
"""Public copy contract for allocation critical-rate thresholds."""

from src.features.allocation.role_selector_help import CRIT_RATE_CAP_HELP, CRIT_THRESHOLD_HELP
from src.features.weighted_allocation.help_text import WEIGHTED_CRIT_THRESHOLD_HELP


def test_critical_rate_help_names_the_exact_included_and_excluded_sources() -> None:
    expected_sources = "5% 基础 + 空幕词条 + 驱动词条 + 额外驱动加成"
    excluded_sources = "不含弧盘、角色成长、武器和其他 Buff"

    assert expected_sources in CRIT_THRESHOLD_HELP
    assert excluded_sources in CRIT_THRESHOLD_HELP
    assert "未达标时优先补暴击" in CRIT_THRESHOLD_HELP
    assert "超过上限的方案无效" not in CRIT_THRESHOLD_HELP
    assert expected_sources in CRIT_RATE_CAP_HELP
    assert excluded_sources in CRIT_RATE_CAP_HELP
    assert "超过上限的方案无效" in CRIT_RATE_CAP_HELP
    assert "未达标时优先补暴击" not in CRIT_RATE_CAP_HELP
    assert expected_sources in WEIGHTED_CRIT_THRESHOLD_HELP
    assert excluded_sources in WEIGHTED_CRIT_THRESHOLD_HELP
