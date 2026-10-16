# 判断角色自选配置适用的分配模式。
"""Mode guards for per-role equipment preference options."""

from __future__ import annotations


def role_preference_mode_error(strategy: str, tape_main_filters=None, crit_priority_modes=None, crit_rate_caps=None) -> str | None:
    if strategy in ("role_priority", "update_mode", "direct_priority", "axis_priority"):
        return None
    return "未知分配策略。"


def allocation_strategy_for_button(button_id: int) -> str:
    """Stable old button IDs keep their meanings when a postprocess option is added."""
    return {0: "role_priority", 1: "update_mode", 2: "direct_priority", 3: "axis_priority"}.get(button_id, "role_priority")
