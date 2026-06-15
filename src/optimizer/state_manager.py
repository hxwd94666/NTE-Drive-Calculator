# 管理已保存配装和锁定状态。
"""Persistence helpers for saved equipment plans and locked items."""

import json
from pathlib import Path
from src.utils.logger import logger


class StateManager:

    def __init__(self, config_dir="config"):
        self.state_file = Path(config_dir) / "equipped_state.json"
        self._ensure_file()

    def _ensure_file(self):
        if not self.state_file.exists():
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text("{}", encoding='utf-8')

    def get_locked_uids(self) -> set:
        with open(self.state_file, 'r', encoding='utf-8') as f:
            state = json.load(f)
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

    def save_allocation(self, final_plan: dict, mode: str = ""):
        with open(self.state_file, 'r', encoding='utf-8') as f:
            old_state = json.load(f)

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
                "strategy_mode": mode
            }

            tape = plan.get("assigned_tape")
            if tape:
                role_data["equipped_tape"] = {
                    "uid": tape.uid,
                    "display_name": f"{tape.set_name}-{tape.main_stats}-{self._format_sub_stats(tape.sub_stats)}",
                    "set_name": tape.set_name,
                    "main_stats": tape.main_stats,
                    "sub_stats": tape.sub_stats,
                    "quality": tape.quality
                }

            drives = plan.get("assigned_set_drives", []) + plan.get("assigned_extra_drives", [])
            for d in drives:
                role_data["equipped_drives"].append({
                    "uid": d.uid,
                    "display_name": f"{d.shape_id}-{self._format_sub_stats(d.sub_stats)}",
                    "shape_id": d.shape_id,
                    "sub_stats": d.sub_stats,
                    "quality": d.quality
                })

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
