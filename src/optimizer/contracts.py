# 定义分配算法输入输出结构，便于新增策略和外部扩展。
"""Typed contracts for allocation strategies.

These types document the dict structures already used by the dispatcher and UI.
They are intentionally lightweight so existing JSON/model data stays compatible.
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict

from src.models.equipment import Drive, Tape


StrategyMode = Literal["role_priority", "drive_priority", "global_optimal"]
STRATEGY_MODES: tuple[StrategyMode, ...] = ("role_priority", "drive_priority", "global_optimal")


class CandidatePool(TypedDict):
    drives: list[Drive]
    tapes: dict[str, list[Tape]]


class Blueprint(TypedDict, total=False):
    set_pieces: list[str]
    extra_pieces: list[str]
    set_effect_mode: str
    board: list[list[str]]


class AllocationPlan(TypedDict):
    valid: bool
    blueprint: NotRequired[Blueprint | dict[str, Any] | None]
    assigned_tape: NotRequired[Tape | None]
    assigned_set_drives: NotRequired[list[Drive]]
    assigned_extra_drives: NotRequired[list[Drive]]
    score: NotRequired[float]


AllocationResult = dict[str, AllocationPlan]
CustomSetMap = dict[str, str]
StatPriorityConfig = dict[str, Any]
StatPriorityConfigMap = dict[str, StatPriorityConfig]

ALLOCATION_PLAN_KEYS = (
    "valid",
    "blueprint",
    "assigned_tape",
    "assigned_set_drives",
    "assigned_extra_drives",
    "score",
)
