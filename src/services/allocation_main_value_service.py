# 冻结配装结果中卡带主词条的满级数值。
"""Pure projections for preserving calculated core main values in plans."""

from __future__ import annotations

from typing import Any

from src.optimizer.contracts import EQUIP_UID, PLAN_ASSIGNED_TAPE
from src.services.allocation_context import AllocationContext
from src.services.allocation_solver import RoleAllocationOption


def legacy_plan_tape_main_values(plan: dict[str, Any]) -> dict[str, float]:
    tape = plan.get(PLAN_ASSIGNED_TAPE)
    if tape is None:
        return {}
    uid = str(tape.get(EQUIP_UID, "") if isinstance(tape, dict) else getattr(tape, EQUIP_UID, ""))
    value = tape.get("main_value") if isinstance(tape, dict) else getattr(tape, "main_value", None)
    try:
        return {uid: float(value)} if uid and value is not None else {}
    except (TypeError, ValueError):
        return {}


def weighted_option_tape_main_values(
    context: AllocationContext, option: RoleAllocationOption,
) -> dict[str, float]:
    candidates = {candidate.uid: candidate for candidate in context.candidates}
    for assignment in option.assignments:
        if assignment.kind != "core" or assignment.virtual:
            continue
        candidate = candidates.get(assignment.uid)
        stat = next(iter(candidate.main_stats), None) if candidate is not None else None
        if stat is None:
            continue
        value = float(stat.value) * (100.0 if stat.percent else 1.0)
        uid = f"nte-core-{assignment.uid[0]}-{assignment.uid[1]}"
        return {uid: round(value, 6)}
    return {}
