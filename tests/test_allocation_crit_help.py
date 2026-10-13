# 测试计算页面的暴击说明。
"""Public copy contract for allocation critical-rate thresholds."""

from src.features.allocation.role_selector_help import CRIT_RATE_CAP_HELP, CRIT_THRESHOLD_HELP


def test_critical_rate_help_names_the_exact_included_and_excluded_sources() -> None:
    expected_sources = "5% 基础 + 卡带/驱动暴击率 + 实际额外驱动加成"

    assert expected_sources in CRIT_THRESHOLD_HELP
    assert "弧盘无条件常驻暴击率 + 已启用好感暴击率" in CRIT_THRESHOLD_HELP
    assert "数值只由你手动修改" in CRIT_THRESHOLD_HELP
    assert "留空不设最小值" in CRIT_THRESHOLD_HELP
    assert "100% − 本次有效弧盘无条件常驻暴击率 − 已启用好感暴击率" in CRIT_RATE_CAP_HELP
    assert "手填 0 表示不限制；自动算出 0 仍是上限" in CRIT_RATE_CAP_HELP
    assert "超过上限的方案无效" in CRIT_RATE_CAP_HELP
    assert "弧盘常驻资料待审查时暂无法核对自动上限" in CRIT_RATE_CAP_HELP
    assert expected_sources in CRIT_RATE_CAP_HELP
