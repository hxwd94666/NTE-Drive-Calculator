# 按正式精炼曲线格式化弧盘技能描述，不由描述文字生成常驻属性。
"""Qt-free formatting helpers for official fork presentation."""

from __future__ import annotations

from typing import Any, Mapping


def render_fork_skill_description(star: Mapping[str, Any]) -> str:
    """按当前曲线值替换正式参数序号；跳过未知参数但保留明确零值，百分比只换算一次。"""

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
