# 计算结果替换后同步当前已保存配装投影：更新换下的卡带/驱动、重算总分并刷新方案差异。
"""替换同步只改写当前窗口持有的已保存展示状态与差异，持久化交给页面既有的保存入口。"""
from __future__ import annotations

import copy

from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.domain.equipment.scoring.allocation_rating import loadout_total_grade
from src.optimizer.contracts import (
    DIFF_CHANGED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_MAIN_VALUE,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_UID,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_SCORE_AREA,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
)
from src.utils.logger import logger


def _apply_saved_role_equipment_diff(self, role_name):
    last_diffs = getattr(self, "_my_role_equipment_last_diffs", {}) or {}
    role_diff = copy.deepcopy(last_diffs.get(role_name) or {})
    if not role_diff.get(DIFF_CHANGED):
        return False
    plan_diffs = dict(getattr(self, "allocation_plan_diff", {}) or {})
    plan_diffs[role_name] = role_diff
    self.allocation_plan_diff = plan_diffs
    return True


def _saved_equipment_main_stat_text(main_stats):
    if isinstance(main_stats, dict):
        return next(iter(main_stats.keys()), "")
    return str(main_stats or "")


def _saved_equipment_score_total(self, role_name, role_state):
    total = 0.0
    tape = role_state.get(ROLE_EQUIPPED_TAPE)
    if isinstance(tape, dict):
        total += float(tape.get(EQUIP_SCORE, 0.0) or 0.0)
    for drive in role_state.get(ROLE_EQUIPPED_DRIVES, []) or []:
        if isinstance(drive, dict):
            total += float(drive.get(EQUIP_SCORE, 0.0) or 0.0)
    role_state[ROLE_TOTAL_SCORE] = round(total, 2)
    role_state[ROLE_TOTAL_GRADE] = loadout_total_grade(total)
    role_state[ROLE_SCORE_AREA] = ALLOCATION_TOTAL_SCORE_AREA


def _persist_saved_equipment_sync(self):
    save = getattr(self, "_save_eq", None)
    if callable(save):
        save()
    refresh = getattr(self, "_refresh_equip", None)
    if callable(refresh):
        try:
            refresh()
        except AttributeError as exc:
            logger.debug(f"刷新配装页面时缺少可选 UI 状态，已忽略: {exc}")


def _sync_saved_drive_replacement(self, role_name, old_uid, new_drive, new_score, new_area):
    state = getattr(self, "equipped_state", {}) or {}
    role_state = state.get(role_name)
    if not isinstance(role_state, dict):
        return False
    drives = role_state.get(ROLE_EQUIPPED_DRIVES, []) or []
    new_uid = str(new_drive.get(EQUIP_UID, "") or "")
    changed = False
    for drive in drives:
        if not isinstance(drive, dict) or str(drive.get(EQUIP_UID, "")) != str(old_uid):
            continue
        drive.update(
            {
                EQUIP_UID: new_uid,
                EQUIP_SHAPE_ID: new_drive.get(EQUIP_SHAPE_ID, ""),
                EQUIP_SUB_STATS: new_drive.get(EQUIP_SUB_STATS, {}) or {},
                EQUIP_QUALITY: new_drive.get(EQUIP_QUALITY, "Gold"),
                EQUIP_AREA: new_area,
                EQUIP_DISPLAY_NAME: new_drive.get(EQUIP_DISPLAY_NAME, ""),
                EQUIP_SCORE: new_score,
                EQUIP_GRADE: self._calc_grade(new_score, new_area),
                EQUIP_SCORE_AREA: new_area,
                EQUIP_IS_CHANGED: True,
            }
        )
        if new_drive.get(EQUIP_MAIN_STATS):
            drive[EQUIP_MAIN_STATS] = new_drive.get(EQUIP_MAIN_STATS)
        drive.pop(EQUIP_IS_NEW, None)
        changed = True
        break
    if changed:
        _saved_equipment_score_total(self, role_name, role_state)
    return changed


def _sync_saved_tape_replacement(self, role_name, new_tape, new_score):
    state = getattr(self, "equipped_state", {}) or {}
    role_state = state.get(role_name)
    if not isinstance(role_state, dict):
        return False
    main_stat = _saved_equipment_main_stat_text(new_tape.get(EQUIP_MAIN_STATS, {}) or {})
    role_state[ROLE_EQUIPPED_TAPE] = {
        EQUIP_UID: str(new_tape.get(EQUIP_UID, "") or ""),
        EQUIP_SET_NAME: new_tape.get(EQUIP_SET_NAME, ""),
        EQUIP_DISPLAY_NAME: new_tape.get(EQUIP_DISPLAY_NAME, ""),
        EQUIP_MAIN_STATS: main_stat,
        EQUIP_MAIN_VALUE: new_tape.get(EQUIP_MAIN_VALUE),
        EQUIP_SUB_STATS: new_tape.get(EQUIP_SUB_STATS, {}) or {},
        EQUIP_QUALITY: new_tape.get(EQUIP_QUALITY, "Gold"),
        EQUIP_SCORE: new_score,
        EQUIP_GRADE: self._calc_grade(new_score, 15),
        EQUIP_SCORE_AREA: 15,
        EQUIP_IS_CHANGED: True,
    }
    _saved_equipment_score_total(self, role_name, role_state)
    return True


def _sync_role_drive_replacement(self, role_name, old_uid, new_drive):
    weights = (getattr(self, "roles_db", {}) or {}).get(role_name, {}).get("weights", {})
    new_uid = str(new_drive.get(EQUIP_UID, "") or "")
    if not old_uid or not new_uid:
        return False

    new_shape = new_drive.get(EQUIP_SHAPE_ID, "")
    new_sub_stats = new_drive.get(EQUIP_SUB_STATS, {}) or {}
    new_quality = new_drive.get(EQUIP_QUALITY, "Gold")
    new_score = self._score_drive_dict(new_sub_stats, new_shape, weights, new_quality)
    new_area = int(new_drive.get(EQUIP_AREA) or getattr(self, "_shape_areas", {}).get(new_shape, 3) or 3)

    saved_changed = _sync_saved_drive_replacement(self, role_name, old_uid, new_drive, new_score, new_area)
    state = getattr(self, "equipped_state", {}) or {}
    role_state = state.get(role_name, {}) if isinstance(state, dict) else {}
    existing_diff = role_state.get(ROLE_LAST_DIFF, {}) if isinstance(role_state, dict) else {}
    if not saved_changed and not existing_diff.get(DIFF_CHANGED):
        return False

    if saved_changed:
        _apply_saved_role_equipment_diff(self, role_name)
        _persist_saved_equipment_sync(self)
    else:
        _apply_saved_role_equipment_diff(self, role_name)
    return True


def _sync_role_tape_replacement(self, role_name, old_uid, new_tape):
    role_cfg = (getattr(self, "roles_db", {}) or {}).get(role_name, {})
    weights = role_cfg.get("weights", {})
    main_weights = role_cfg.get("main_weights")
    new_uid = str(new_tape.get(EQUIP_UID, "") or "")
    if not new_uid:
        return False

    main_stats = new_tape.get(EQUIP_MAIN_STATS, {}) or {}
    main_stat = next(iter(main_stats.keys()), "") if isinstance(main_stats, dict) else str(main_stats or "")
    sub_stats = new_tape.get(EQUIP_SUB_STATS, {}) or {}
    quality = new_tape.get(EQUIP_QUALITY, "Gold")
    new_score = self._score_tape_dict(main_stat, sub_stats, weights, quality, main_weights)

    saved_changed = _sync_saved_tape_replacement(self, role_name, new_tape, new_score)
    state = getattr(self, "equipped_state", {}) or {}
    role_state = state.get(role_name, {}) if isinstance(state, dict) else {}
    existing_diff = role_state.get(ROLE_LAST_DIFF, {}) if isinstance(role_state, dict) else {}
    if not saved_changed and not existing_diff.get(DIFF_CHANGED):
        return False
    if saved_changed:
        _apply_saved_role_equipment_diff(self, role_name)
        _persist_saved_equipment_sync(self)
    else:
        _apply_saved_role_equipment_diff(self, role_name)
    return True
