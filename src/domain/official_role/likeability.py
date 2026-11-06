# 区分未启用与属性资料未知，避免面板、评分和保存将缺项默认为零。
from collections.abc import Mapping
from typing import Any


def likeability_unavailable(bonus: Mapping[str, Any] | None) -> bool:
    return (bonus or {}).get("availability") == "unavailable"


def likeability_input_issue(detail: Mapping[str, Any]) -> str:
    if bool((detail.get("profile") or {}).get("likeability_level_10_enabled")) and likeability_unavailable(detail.get("likeability_bonus")):
        return "好感度十级属性资料缺失；请关闭本次十级好感选项后计算，或更新同版资料。"
    return ""


def enabled_likeability_properties(bonus: Mapping[str, Any] | None, *, enabled: bool):
    if not enabled:
        return ()
    issue = likeability_input_issue({"profile": {"likeability_level_10_enabled": enabled}, "likeability_bonus": bonus})
    if issue:
        raise ValueError(issue)
    return tuple((bonus or {}).get("properties") or ())
