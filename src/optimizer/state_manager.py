# 管理已保存配装和锁定状态。
"""Persistence helpers for saved equipment plans and locked items."""

import json
from pathlib import Path
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
from src.optimizer.contracts import (
    DIFF_ADDED,
    DIFF_ADDED_UIDS,
    DIFF_CHANGED,
    DIFF_REMOVED,
    EQUIP_AREA,
    EQUIP_DISPLAY_NAME,
    EQUIP_GRADE,
    EQUIP_IS_CHANGED,
    EQUIP_IS_NEW,
    EQUIP_MAIN_STATS,
    EQUIP_QUALITY,
    EQUIP_SCORE,
    EQUIP_SCORE_AREA,
    EQUIP_SET_NAME,
    EQUIP_SHAPE_ID,
    EQUIP_SUB_STATS,
    EQUIP_TYPE,
    EQUIP_UID,
    PLAN_ASSIGNED_TAPE,
    PLAN_BLUEPRINT,
    PLAN_SCORE,
    PLAN_VALID,
    ROLE_BLUEPRINT_LAYOUT,
    ROLE_EQUIPPED_DRIVES,
    ROLE_EQUIPPED_TAPE,
    ROLE_LAST_DIFF,
    ROLE_SCORE_AREA,
    ROLE_STRATEGY_MODE,
    ROLE_TOTAL_GRADE,
    ROLE_TOTAL_SCORE,
    plan_drives,
)
from src.utils.logger import logger


class StateManager:

    def __init__(self, config_dir="config"):
        self.state_file = Path(config_dir) / "equipped_state.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.state_file.exists():
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text("{}", encoding='utf-8')

    def load_state(self) -> dict:
        with open(self.state_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    def get_locked_uids(self) -> set:
        state = self.load_state()
        locked_uids = set()

        for role_data in state.values():
            if isinstance(role_data, list):
                locked_uids.update(role_data)
            elif isinstance(role_data, dict):
                if role_data.get(ROLE_EQUIPPED_TAPE):
                    locked_uids.add(role_data[ROLE_EQUIPPED_TAPE][EQUIP_UID])
                for d in role_data.get(ROLE_EQUIPPED_DRIVES, []):
                    locked_uids.add(d[EQUIP_UID])
        return locked_uids

    def _format_sub_stats(self, sub_stats: dict) -> str:
        return "|".join(f"{k}_{v}" for k, v in sub_stats.items())

    def _grade_tag(self, score: float, area: int) -> str:
        max_score = float(area or 0) * 10.0
        if max_score <= 0:
            return "D"
        ratio = float(score or 0.0) / max_score
        if ratio >= 0.8:
            return "ACE"
        if ratio >= 0.7:
            return "SSS"
        if ratio >= 0.6:
            return "SS"
        if ratio >= 0.5:
            return "S"
        if ratio >= 0.4:
            return "A"
        if ratio >= 0.3:
            return "B"
        if ratio >= 0.2:
            return "C"
        return "D"

    def _item_map(self, role_data: dict) -> dict:
        if isinstance(role_data, list):
            return {str(uid): {EQUIP_UID: str(uid), EQUIP_TYPE: "equipment", EQUIP_DISPLAY_NAME: str(uid)} for uid in role_data if uid}
        if not isinstance(role_data, dict):
            return {}
        items = {}
        tape = role_data.get(ROLE_EQUIPPED_TAPE)
        if isinstance(tape, dict) and tape.get(EQUIP_UID):
            items[tape[EQUIP_UID]] = self._saved_item_snapshot(tape, "tape")
        for drive in role_data.get(ROLE_EQUIPPED_DRIVES, []) or []:
            if isinstance(drive, dict) and drive.get(EQUIP_UID):
                items[drive[EQUIP_UID]] = self._saved_item_snapshot(drive, "drive")
        return items

    def _saved_item_snapshot(self, item: dict, item_type: str) -> dict:
        snapshot = {
            EQUIP_UID: str(item.get(EQUIP_UID, "")),
            EQUIP_TYPE: item_type,
            EQUIP_DISPLAY_NAME: str(item.get(EQUIP_DISPLAY_NAME) or item.get(EQUIP_UID) or ""),
        }
        for key in (EQUIP_SHAPE_ID, EQUIP_SET_NAME, EQUIP_MAIN_STATS, EQUIP_SUB_STATS, EQUIP_QUALITY, EQUIP_SCORE, EQUIP_GRADE, EQUIP_SCORE_AREA, EQUIP_AREA):
            if key in item:
                snapshot[key] = item[key]
        return snapshot

    def _build_role_diff(self, old_data: dict, new_data: dict) -> dict:
        old_items = self._item_map(old_data)
        new_items = self._item_map(new_data)
        if not old_items:
            return {DIFF_CHANGED: False, DIFF_ADDED_UIDS: [], DIFF_ADDED: [], DIFF_REMOVED: []}
        old_uids = set(old_items)
        new_uids = set(new_items)
        added_uids = new_uids - old_uids
        removed_uids = old_uids - new_uids
        return {
            DIFF_CHANGED: bool(added_uids or removed_uids),
            DIFF_ADDED_UIDS: list(added_uids),
            DIFF_ADDED: [item for uid, item in new_items.items() if uid in added_uids],
            DIFF_REMOVED: [item for uid, item in old_items.items() if uid in removed_uids],
        }

    def _changed_uids(self, role_data: dict) -> set[str]:
        if not isinstance(role_data, dict):
            return set()
        changed = set()
        tape = role_data.get(ROLE_EQUIPPED_TAPE)
        if isinstance(tape, dict) and tape.get(EQUIP_UID) and tape.get(EQUIP_IS_CHANGED):
            changed.add(str(tape[EQUIP_UID]))
        for drive in role_data.get(ROLE_EQUIPPED_DRIVES, []) or []:
            if isinstance(drive, dict) and drive.get(EQUIP_UID) and drive.get(EQUIP_IS_CHANGED):
                changed.add(str(drive[EQUIP_UID]))
        return changed

    def _merge_changed_kept_diff(self, role_diff: dict, old_data: dict, new_data: dict) -> dict:
        old_items = self._item_map(old_data)
        new_items = self._item_map(new_data)
        changed_kept = self._changed_uids(old_data) & set(new_items)
        if not changed_kept:
            return role_diff
        added_uids = set(role_diff.get(DIFF_ADDED_UIDS, []) or [])
        role_diff[DIFF_CHANGED] = True
        role_diff[DIFF_ADDED_UIDS] = list(added_uids | changed_kept)
        existing_added = {item.get(EQUIP_UID) for item in role_diff.get(DIFF_ADDED, []) if isinstance(item, dict)}
        existing_removed = {item.get(EQUIP_UID) for item in role_diff.get(DIFF_REMOVED, []) if isinstance(item, dict)}
        role_diff.setdefault(DIFF_ADDED, [])
        role_diff.setdefault(DIFF_REMOVED, [])
        for uid in changed_kept:
            if uid in new_items and uid not in existing_added:
                role_diff[DIFF_ADDED].append(new_items[uid])
            if uid in old_items and uid not in existing_removed:
                role_diff[DIFF_REMOVED].append(old_items[uid])
        return role_diff

    def save_allocation(self, final_plan: dict, mode: str = ""):
        old_state = self.load_state()

        new_state = {}
        # 继承未参与本次统筹的角色
        for role, data in old_state.items():
            if role not in final_plan:
                new_state[role] = data

        for role, plan in final_plan.items():
            if not plan or not plan.get(PLAN_VALID):
                if role in old_state:
                    new_state[role] = old_state[role]
                continue

            raw_board = plan.get(PLAN_BLUEPRINT, {}).get("board", [])
            formatted_board = []
            for row in raw_board:
                formatted_row = []
                for cell in row:
                    if cell == -1:
                        formatted_row.append("XX")
                    elif cell == 0:
                        formatted_row.append("0")
                    else:
                        formatted_row.append(str(cell))
                formatted_board.append(formatted_row)

            role_data = {
                ROLE_BLUEPRINT_LAYOUT: formatted_board,
                ROLE_EQUIPPED_TAPE: None,
                ROLE_EQUIPPED_DRIVES: [],
                ROLE_STRATEGY_MODE: mode,
                ROLE_TOTAL_SCORE: round(float(plan.get(PLAN_SCORE, 0.0) or 0.0), 2),
                ROLE_TOTAL_GRADE: self._grade_tag(plan.get(PLAN_SCORE, 0.0), ALLOCATION_TOTAL_SCORE_AREA),
                ROLE_SCORE_AREA: ALLOCATION_TOTAL_SCORE_AREA,
            }

            tape = plan.get(PLAN_ASSIGNED_TAPE)
            if tape:
                tape_score = round(float(tape.role_scores.get(role, 0.0)), 2)
                role_data[ROLE_EQUIPPED_TAPE] = {
                    EQUIP_UID: tape.uid,
                    EQUIP_DISPLAY_NAME: f"{tape.set_name}-{tape.main_stats}-{self._format_sub_stats(tape.sub_stats)}",
                    EQUIP_SET_NAME: tape.set_name,
                    EQUIP_MAIN_STATS: tape.main_stats,
                    EQUIP_SUB_STATS: tape.sub_stats,
                    EQUIP_QUALITY: tape.quality,
                    EQUIP_SCORE: tape_score,
                    EQUIP_GRADE: self._grade_tag(tape_score, 15),
                    EQUIP_SCORE_AREA: 15,
                }

            drives = plan_drives(plan)
            for d in drives:
                drive_score = round(float(d.role_scores.get(role, 0.0)), 2)
                role_data[ROLE_EQUIPPED_DRIVES].append({
                    EQUIP_UID: d.uid,
                    EQUIP_DISPLAY_NAME: f"{d.shape_id}-{self._format_sub_stats(d.sub_stats)}",
                    EQUIP_SHAPE_ID: d.shape_id,
                    EQUIP_SUB_STATS: d.sub_stats,
                    EQUIP_QUALITY: d.quality,
                    EQUIP_SCORE: drive_score,
                    EQUIP_GRADE: self._grade_tag(drive_score, d.area),
                    EQUIP_SCORE_AREA: d.area,
                })

            role_diff = self._build_role_diff(old_state.get(role), role_data)
            role_diff = self._merge_changed_kept_diff(role_diff, old_state.get(role), role_data)
            if role_diff[DIFF_CHANGED]:
                added_uids = set(role_diff[DIFF_ADDED_UIDS])
                if role_data.get(ROLE_EQUIPPED_TAPE) and role_data[ROLE_EQUIPPED_TAPE][EQUIP_UID] in added_uids:
                    role_data[ROLE_EQUIPPED_TAPE][EQUIP_IS_NEW] = True
                for drive_data in role_data.get(ROLE_EQUIPPED_DRIVES, []):
                    if drive_data[EQUIP_UID] in added_uids:
                        drive_data[EQUIP_IS_NEW] = True
                role_data[ROLE_LAST_DIFF] = role_diff

            new_state[role] = role_data

            self._print_diff(role, old_state.get(role), role_data)

        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(new_state, f, ensure_ascii=False, indent=4)
        logger.success("状态持久化完毕，配置已锁定。")

    def _print_diff(self, role: str, old_data: dict, new_data: dict):
        if not old_data or isinstance(old_data, list):
            logger.info(f"[{role}] 部署了全新配装方案。")
            return

        old_items = {}
        if old_data.get(ROLE_EQUIPPED_TAPE):
            old_items[old_data[ROLE_EQUIPPED_TAPE][EQUIP_UID]] = old_data[ROLE_EQUIPPED_TAPE][EQUIP_DISPLAY_NAME]
        for d in old_data.get(ROLE_EQUIPPED_DRIVES, []):
            old_items[d[EQUIP_UID]] = d[EQUIP_DISPLAY_NAME]

        new_items = {}
        if new_data.get(ROLE_EQUIPPED_TAPE):
            new_items[new_data[ROLE_EQUIPPED_TAPE][EQUIP_UID]] = new_data[ROLE_EQUIPPED_TAPE][EQUIP_DISPLAY_NAME]
        for d in new_data.get(ROLE_EQUIPPED_DRIVES, []):
            new_items[d[EQUIP_UID]] = d[EQUIP_DISPLAY_NAME]

        old_uids, new_uids = set(old_items.keys()), set(new_items.keys())
        removed = old_uids - new_uids
        added = new_uids - old_uids

        if not removed and not added:
            logger.info(f"  [{role}] 配装方案未变更。")
            return

        logger.warning(f"[{role}] 装备发生变更:")
        for u in removed:
            logger.opt(raw=True).info(f"  [-] 卸下: {old_items[u]}\n")
        for u in added:
            logger.opt(raw=True).info(f"  [+] 穿上: {new_items[u]}\n")
