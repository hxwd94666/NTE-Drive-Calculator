# 汇总配装装备评分并生成展示数据。
"""Shared scoring projection for equipment display states."""

from __future__ import annotations

from typing import Any

from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.domain.allocation_rating import allocation_grade
from src.optimizer.contracts import (
    EQUIP_GRADE,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
)


def score_equipment_display_state(
    presentation: Any,
    role_name: str,
    state: dict[str, Any],
    roles_db: dict[str, Any],
) -> tuple[float, dict[str, float]]:
    """Score one projected loadout and persist the result in its display model."""

    role_cfg = roles_db.get(role_name, {}) or {}
    weights = role_cfg.get("weights", {})
    main_weights = role_cfg.get("main_weights")
    scores: dict[str, float] = {}

    tape = state.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, dict):
        score = float(
            presentation.score_tape(
                tape.get(EQUIP_MAIN_STATS, ""),
                tape.get(EQUIP_SUB_STATS, {}),
                weights,
                tape.get(EQUIP_QUALITY, "Gold"),
                main_weights,
            )
        )
        tape[EQUIP_SCORE] = score
        tape[EQUIP_GRADE] = allocation_grade(score, 15)
        uid = str(tape.get(EQUIP_UID) or "")
        if uid:
            scores[uid] = score

    for drive in state.get(ROLE_EQUIPPED_DRIVES, ()) or ():
        if not isinstance(drive, dict):
            continue
        score = float(
            presentation.score_drive(
                drive.get(EQUIP_SUB_STATS, {}),
                drive.get(EQUIP_SHAPE_ID, ""),
                weights,
                drive.get(EQUIP_QUALITY, "Gold"),
            )
        )
        drive[EQUIP_SCORE] = score
        area = int(presentation.shape_area(drive.get(EQUIP_SHAPE_ID, ""), 3))
        drive[EQUIP_GRADE] = allocation_grade(score, area)
        uid = str(drive.get(EQUIP_UID) or "")
        if uid:
            scores[uid] = score

    total = round(sum(scores.values()), 6)
    state[ROLE_TOTAL_SCORE] = total
    state[ROLE_TOTAL_GRADE] = allocation_grade(
        total,
        ALLOCATION_TOTAL_SCORE_AREA,
    )
    return total, scores
