# 格式化官方弧盘常驻属性及养成状态的玩家可读摘要。
"""Qt-free formatting helpers for official fork presentation."""

from __future__ import annotations

from typing import Any, Mapping


def render_fork_skill_description(star: Mapping[str, Any]) -> str:
    """Render official refinement placeholders with current curve values."""

    description = str(star.get("description_zh") or "")
    for parameter in star.get("parameters") or ():
        if not isinstance(parameter, Mapping) or parameter.get("value") is None:
            continue
        number = float(parameter["value"]) * (
            100.0 if parameter.get("is_percent") else 1.0
        )
        shown = f"{number:.6f}".rstrip("0").rstrip(".")
        if parameter.get("is_percent"):
            shown += "%"
        description = description.replace(
            "{" + str(int(parameter.get("ordinal") or 0)) + "}",
            shown,
        )
    return description.replace("<lv>", "").replace("</>", "")
