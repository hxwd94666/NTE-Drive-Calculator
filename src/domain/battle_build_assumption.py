# 定义战报毕业配装假定的冻结来源与只读投影。
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any


GRADUATION_ASSUMPTION_TITLE = "毕业模板假定（缺少原生背包）"
GRADUATION_ASSUMPTION_WARNING = (
    "未取得完整原生背包，本场使用毕业模板假定空幕/驱动进行计算；实测战报已保留。"
)


def assumed_graduation_equipment(profile: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    assumption = profile.get("equipment_assumption")
    if not isinstance(assumption, Mapping):
        return None
    if assumption.get("kind") != "official_graduation" or assumption.get("version") != 1:
        return None
    items = assumption.get("items")
    if not isinstance(items, list):
        return None
    return deepcopy(items)


def has_graduation_assumption(build: Mapping[str, Any] | None) -> bool:
    return any(
        assumed_graduation_equipment(role.get("profile") or {}) is not None
        for role in (build or {}).get("characters") or ()
    )
