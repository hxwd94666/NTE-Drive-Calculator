"""Compact, actionable summaries for the fast-equipment completion dialog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def build_fast_apply_completion_summary(
    applied: Sequence[Mapping[str, Any]],
    *,
    mismatch_role_names: frozenset[str] = frozenset(),
) -> tuple[str, str]:
    """Return dispatched equipment counts without claiming fragment verification.

    Equipment residual events are not a complete inventory view.  They remain
    useful for internal retry diagnostics, but presenting their absence or a
    partial match as a player-visible loadout result would be misleading.
    """

    del mismatch_role_names
    last_attempt = max((int(row.get("attempt_count") or 1) for row in applied), default=1)

    summary = f"已下发 {len(applied)} 个角色的配装"
    if last_attempt > 1:
        summary += f"（已进行至第 {last_attempt} 轮装配）"
    lines = []
    for row in applied:
        role_name = str(row.get("role_name") or "未知角色")
        module_count = row.get("module_count")
        if module_count is None:
            detail = "已下发"
        else:
            detail = f"{int(module_count)} 个驱动"
            if row.get("core_count"):
                detail += " + 1 个核心"
        lines.append(f"• {role_name}：{detail}")
    return summary, "\n".join(lines)
