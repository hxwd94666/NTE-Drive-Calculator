# 汇总配装装备评分并生成展示数据。
"""Shared scoring projection for equipment display states."""

from __future__ import annotations

from typing import Any

from src.domain.equipment.scoring.allocation_rating import allocation_grade, loadout_total_grade
from src.domain.equipment.loadouts.loadout_plan_scores import accumulate_assignment_scores
from src.services.equipment.equipment_scoring_service import prepare_equipment_stat_scores
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


def score_equipment_display_states(presentation, states, roles_db):
    """先批量预热再复用单件入口；限制交叉矩阵，避免大名单挤掉尚未消费的缓存。"""
    engine = getattr(presentation, 'scoring_engine', None)
    batch, items, profiles = [], [], []

    def flush():
        if engine is not None and items:
            prepare_equipment_stat_scores(engine, items, profiles)
        for role, state in batch:
            score_equipment_display_state(presentation, role, state, roles_db)
        batch.clear()
        items.clear()
        profiles.clear()

    for role, state in states.items():
        if not isinstance(state, dict):
            continue
        current = []
        tape = state.get(ROLE_EQUIPPED_TAPE)
        if isinstance(tape, dict):
            current.append(dict(item_type='tape', sub_stats=tape.get(EQUIP_SUB_STATS, {}),
                main_stats=tape.get(EQUIP_MAIN_STATS, ''), quality=tape.get(EQUIP_QUALITY, 'Gold'),
                main_value=tape.get('main_value')))
        for drive in state.get(ROLE_EQUIPPED_DRIVES, ()) or ():
            if isinstance(drive, dict):
                current.append(dict(item_type='drive', sub_stats=drive.get(EQUIP_SUB_STATS, {}),
                    area=presentation.shape_area(drive.get(EQUIP_SHAPE_ID, ''), 3),
                    quality=drive.get(EQUIP_QUALITY, 'Gold')))
        # 预热最多占用一半左右的评分缓存，留出单件适配和等级查询的空间；逐批立刻消费。
        if batch and (len(items) + len(current)) * (len(profiles) + 1) > 4096:
            flush()
        config = roles_db.get(role, {}) or {}
        mains = config.get('main_weights')
        profiles.append(dict(key=role, weights=dict(config.get('weights', {})),
                             main_weights=dict(mains) if isinstance(mains, dict) else None))
        items.extend(current)
        batch.append((role, state))
    flush()


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
                tape.get("main_value"),
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

    total = accumulate_assignment_scores(scores.values())
    state[ROLE_TOTAL_SCORE] = total
    state[ROLE_TOTAL_GRADE] = loadout_total_grade(total)
    return total, scores
