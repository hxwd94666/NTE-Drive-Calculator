# 管理已保存配装和锁定状态。
"""Persistence helpers for saved equipment plans and locked items."""

import json
from pathlib import Path
from src.app.constants import ALLOCATION_TOTAL_SCORE_AREA
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
                if role_data.get("equipped_tape"):
                    locked_uids.add(role_data["equipped_tape"]["uid"])
                for d in role_data.get("equipped_drives", []):
                    locked_uids.add(d["uid"])
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
            return {str(uid): {"uid": str(uid), "type": "equipment", "display_name": str(uid)} for uid in role_data if uid}
        if not isinstance(role_data, dict):
            return {}
        items = {}
        tape = role_data.get("equipped_tape")
        if isinstance(tape, dict) and tape.get("uid"):
            items[tape["uid"]] = self._saved_item_snapshot(tape, "tape")
        for drive in role_data.get("equipped_drives", []) or []:
            if isinstance(drive, dict) and drive.get("uid"):
                items[drive["uid"]] = self._saved_item_snapshot(drive, "drive")
        return items

    def _saved_item_snapshot(self, item: dict, item_type: str) -> dict:
        snapshot = {
            "uid": str(item.get("uid", "")),
            "type": item_type,
            "display_name": str(item.get("display_name") or item.get("uid") or ""),
        }
        for key in ("shape_id", "set_name", "main_stats", "sub_stats", "quality", "score", "grade", "score_area", "area"):
            if key in item:
                snapshot[key] = item[key]
        return snapshot

    def _build_role_diff(self, old_data: dict, new_data: dict) -> dict:
        old_items = self._item_map(old_data)
        new_items = self._item_map(new_data)
        if not old_items:
            return {"changed": False, "added_uids": [], "added": [], "removed": []}
        old_uids = set(old_items)
        new_uids = set(new_items)
        added_uids = new_uids - old_uids
        removed_uids = old_uids - new_uids
        return {
            "changed": bool(added_uids or removed_uids),
            "added_uids": list(added_uids),
            "added": [item for uid, item in new_items.items() if uid in added_uids],
            "removed": [item for uid, item in old_items.items() if uid in removed_uids],
        }

    def _changed_uids(self, role_data: dict) -> set[str]:
        if not isinstance(role_data, dict):
            return set()
        changed = set()
        tape = role_data.get("equipped_tape")
        if isinstance(tape, dict) and tape.get("uid") and tape.get("is_changed"):
            changed.add(str(tape["uid"]))
        for drive in role_data.get("equipped_drives", []) or []:
            if isinstance(drive, dict) and drive.get("uid") and drive.get("is_changed"):
                changed.add(str(drive["uid"]))
        return changed

    def _merge_changed_kept_diff(self, role_diff: dict, old_data: dict, new_data: dict) -> dict:
        old_items = self._item_map(old_data)
        new_items = self._item_map(new_data)
        changed_kept = self._changed_uids(old_data) & set(new_items)
        if not changed_kept:
            return role_diff
        added_uids = set(role_diff.get("added_uids", []) or [])
        role_diff["changed"] = True
        role_diff["added_uids"] = list(added_uids | changed_kept)
        existing_added = {item.get("uid") for item in role_diff.get("added", []) if isinstance(item, dict)}
        existing_removed = {item.get("uid") for item in role_diff.get("removed", []) if isinstance(item, dict)}
        role_diff.setdefault("added", [])
        role_diff.setdefault("removed", [])
        for uid in changed_kept:
            if uid in new_items and uid not in existing_added:
                role_diff["added"].append(new_items[uid])
            if uid in old_items and uid not in existing_removed:
                role_diff["removed"].append(old_items[uid])
        return role_diff

    def save_allocation(self, final_plan: dict, mode: str = ""):
        old_state = self.load_state()

        new_state = {}
        # 继承未参与本次统筹的角色
        for role, data in old_state.items():
            if role not in final_plan:
                new_state[role] = data

        for role, plan in final_plan.items():
            if not plan or not plan.get('valid'):
                if role in old_state:
                    new_state[role] = old_state[role]
                continue

            raw_board = plan.get("blueprint", {}).get("board", [])
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
                "blueprint_layout": formatted_board,
                "equipped_tape": None,
                "equipped_drives": [],
                "strategy_mode": mode,
                "total_score": round(float(plan.get("score", 0.0) or 0.0), 2),
                "total_grade": self._grade_tag(plan.get("score", 0.0), ALLOCATION_TOTAL_SCORE_AREA),
                "score_area": ALLOCATION_TOTAL_SCORE_AREA,
            }

            tape = plan.get("assigned_tape")
            if tape:
                tape_score = round(float(tape.role_scores.get(role, 0.0)), 2)
                role_data["equipped_tape"] = {
                    "uid": tape.uid,
                    "display_name": f"{tape.set_name}-{tape.main_stats}-{self._format_sub_stats(tape.sub_stats)}",
                    "set_name": tape.set_name,
                    "main_stats": tape.main_stats,
                    "sub_stats": tape.sub_stats,
                    "quality": tape.quality,
                    "score": tape_score,
                    "grade": self._grade_tag(tape_score, 15),
                    "score_area": 15,
                }

            drives = plan.get("assigned_set_drives", []) + plan.get("assigned_extra_drives", [])
            for d in drives:
                drive_score = round(float(d.role_scores.get(role, 0.0)), 2)
                role_data["equipped_drives"].append({
                    "uid": d.uid,
                    "display_name": f"{d.shape_id}-{self._format_sub_stats(d.sub_stats)}",
                    "shape_id": d.shape_id,
                    "sub_stats": d.sub_stats,
                    "quality": d.quality,
                    "score": drive_score,
                    "grade": self._grade_tag(drive_score, d.area),
                    "score_area": d.area,
                })

            role_diff = self._build_role_diff(old_state.get(role), role_data)
            role_diff = self._merge_changed_kept_diff(role_diff, old_state.get(role), role_data)
            if role_diff["changed"]:
                added_uids = set(role_diff["added_uids"])
                if role_data.get("equipped_tape") and role_data["equipped_tape"]["uid"] in added_uids:
                    role_data["equipped_tape"]["is_new"] = True
                for drive_data in role_data.get("equipped_drives", []):
                    if drive_data["uid"] in added_uids:
                        drive_data["is_new"] = True
                role_data["last_diff"] = role_diff

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
        if old_data.get("equipped_tape"):
            old_items[old_data["equipped_tape"]["uid"]] = old_data["equipped_tape"]["display_name"]
        for d in old_data.get("equipped_drives", []):
            old_items[d["uid"]] = d["display_name"]

        new_items = {}
        if new_data.get("equipped_tape"):
            new_items[new_data["equipped_tape"]["uid"]] = new_data["equipped_tape"]["display_name"]
        for d in new_data.get("equipped_drives", []):
            new_items[d["uid"]] = d["display_name"]

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
