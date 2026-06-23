# 判断角色自选配置适用的分配模式。
"""Mode guards for per-role equipment preference options."""

from __future__ import annotations


def has_role_preference_configs(tape_main_filters, crit_priority_modes) -> bool:
    if tape_main_filters:
        return True
    for config in (crit_priority_modes or {}).values():
        if isinstance(config, dict) and config.get("stats"):
            return True
    return False


def role_preference_mode_error(strategy: str, tape_main_filters=None, crit_priority_modes=None) -> str | None:
    if strategy in ("role_priority", "update_mode"):
        return None
    if not has_role_preference_configs(tape_main_filters, crit_priority_modes):
        return None
    return "词条自选功能仅限于角色优先或增量更新模式。请切换分配策略，或清空卡带主词条自选和驱动词条自选。"
