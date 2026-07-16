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

PLAN_VALID = "valid"
PLAN_BLUEPRINT = "blueprint"
PLAN_ASSIGNED_TAPE = "assigned_tape"
PLAN_ASSIGNED_SET_DRIVES = "assigned_set_drives"
PLAN_ASSIGNED_EXTRA_DRIVES = "assigned_extra_drives"
PLAN_SCORE = "score"
PLAN_CHANGED_UIDS = "changed_uids"
PLAN_CUSTOM_WEAPON = "custom_weapon"

EQUIP_UID = "uid"
EQUIP_TYPE = "type"
EQUIP_ITEM_TYPE = "item_type"
EQUIP_DISPLAY_NAME = "display_name"
EQUIP_SHAPE_ID = "shape_id"
EQUIP_SET_NAME = "set_name"
EQUIP_MAIN_STATS = "main_stats"
EQUIP_SUB_STATS = "sub_stats"
EQUIP_QUALITY = "quality"
EQUIP_SCORE = "score"
EQUIP_GRADE = "grade"
EQUIP_SCORE_AREA = "score_area"
EQUIP_AREA = "area"
EQUIP_IS_NEW = "is_new"
EQUIP_IS_CHANGED = "is_changed"

ROLE_BLUEPRINT_LAYOUT = "blueprint_layout"
ROLE_EQUIPPED_TAPE = "equipped_tape"
ROLE_EQUIPPED_DRIVES = "equipped_drives"
ROLE_STRATEGY_MODE = "strategy_mode"
ROLE_TOTAL_SCORE = "total_score"
ROLE_TOTAL_GRADE = "total_grade"
ROLE_SCORE_AREA = "score_area"
ROLE_LAST_DIFF = "last_diff"

DIFF_CHANGED = "changed"
DIFF_ADDED_UIDS = "added_uids"
DIFF_ADDED = "added"
DIFF_REMOVED = "removed"


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
    changed_uids: NotRequired[set[str]]
    custom_weapon: NotRequired[str]


class EquipmentSnapshot(TypedDict, total=False):
    uid: str
    type: str
    item_type: str
    display_name: str
    shape_id: str
    set_name: str
    main_stats: str | dict[str, Any]
    sub_stats: dict[str, Any]
    quality: str
    score: float
    grade: str
    score_area: int
    area: int
    is_new: bool
    is_changed: bool


class PlanDiff(TypedDict):
    changed: bool
    added_uids: list[str] | set[str]
    added: list[EquipmentSnapshot]
    removed: list[EquipmentSnapshot]


class RoleEquipmentState(TypedDict, total=False):
    blueprint_layout: list[list[str]]
    equipped_tape: EquipmentSnapshot | None
    equipped_drives: list[EquipmentSnapshot]
    strategy_mode: str
    total_score: float
    total_grade: str
    score_area: int
    last_diff: PlanDiff


AllocationResult = dict[str, AllocationPlan]
CustomSetMap = dict[str, str]
StatPriorityConfig = dict[str, Any]
StatPriorityConfigMap = dict[str, StatPriorityConfig]

ALLOCATION_PLAN_KEYS = (
    PLAN_VALID,
    PLAN_BLUEPRINT,
    PLAN_ASSIGNED_TAPE,
    PLAN_ASSIGNED_SET_DRIVES,
    PLAN_ASSIGNED_EXTRA_DRIVES,
    PLAN_SCORE,
)

EQUIPMENT_SNAPSHOT_KEYS = (
    EQUIP_UID,
    EQUIP_TYPE,
    EQUIP_DISPLAY_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SET_NAME,
    EQUIP_MAIN_STATS,
    EQUIP_SUB_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_GRADE,
    EQUIP_SCORE_AREA,
    EQUIP_AREA,
)


def plan_drives(plan: AllocationPlan | dict[str, Any]) -> list[Drive]:
    return list(plan.get(PLAN_ASSIGNED_SET_DRIVES, []) or []) + list(plan.get(PLAN_ASSIGNED_EXTRA_DRIVES, []) or [])
