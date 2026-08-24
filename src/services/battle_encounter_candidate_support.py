# 不含推断状态的遇敌候选构造辅助。
"""Small candidate builders shared by battle encounter inference."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def world_boss_candidates(
    static_dao: Any,
    *,
    target_preset: Callable[..., Any],
    candidate_factory: Callable[..., Any],
) -> tuple[Any, ...]:
    result = []
    for row in static_dao.list_world_boss_target_fingerprint_rows():
        target = target_preset(
            row,
            target_id=row.get("target_id"),
            target_name=row.get("name_zh"),
            monster_class_path=row.get("monster_template_name"),
        )
        if target is None:
            continue
        level = int(row.get("monster_level") or 0)
        result.append(candidate_factory(
            environment_kind="open_world",
            environment_ref=f"world_boss|{target.target_id}|{level}",
            environment_name=f"异象追猎 · {target.target_name} · Lv.{level}",
            scope_half="",
            outer_realm_floor=None,
            difficulty_id=None,
            feast_options=(),
            targets=(target,),
        ))
    return tuple(result)
