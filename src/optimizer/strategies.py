"""Allocation strategies for role-first, item-first, and global matching."""

import copy
import itertools
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List, Dict, Any

from src.utils.logger import logger
from src.utils.name_resolver import resolve_name
from src.models.equipment import Drive, Tape

class BaseDispatchStrategy:
    def __init__(self, roles_db: Dict, sets_db: Dict, blueprints_db: Dict[str, List[Dict]]):
        self.roles_db = roles_db
        self.sets_db = sets_db
        self.blueprints_db = blueprints_db

    def _resolve_set_name(self, set_name: str) -> str:
        resolved = resolve_name(set_name, self.sets_db.keys(), cutoff=0.78)
        return resolved or set_name

    def _target_set(self, role: str, custom_sets: Dict[str, str]) -> str:
        raw_set = (custom_sets or {}).get(role, self.roles_db[role]["default_set"])
        target_set = self._resolve_set_name(raw_set)
        if target_set not in self.sets_db:
            raise ValueError(f"错误：指定的套装 {raw_set} 不存在于 sets.json 中！")
        return target_set

    def _pre_allocate_tapes(self, priority_list: List[str], custom_sets: Dict[str, str],
                            tapes_pool: Dict[str, List[Tape]]) -> Dict[str, Tape]:
        assigned_tapes = {}
        used_tape_uids = set()

        for role in priority_list:
            target_set = self._target_set(role, custom_sets)
            role_tapes = tapes_pool.get(role, [])

            best_tape = None
            best_score = -1.0

            for tape in role_tapes:
                tape_set = self._resolve_set_name(tape.set_name)
                if tape_set != tape.set_name and tape_set in self.sets_db:
                    tape.set_name = tape_set
                if tape.uid not in used_tape_uids and tape.set_name == target_set:
                    score = tape.role_scores.get(role, 0.0)
                    if score > best_score:
                        best_score, best_tape = score, tape

            if best_tape:
                assigned_tapes[role] = best_tape
                used_tape_uids.add(best_tape.uid)
            else:
                assigned_tapes[role] = None

        return assigned_tapes

    def execute(self, candidate_pool: Dict[str, Any], priority_list: List[str], custom_sets: Dict[str, str]) -> Dict[str, Any]:
        raise NotImplementedError

class RolePriorityStrategy(BaseDispatchStrategy):
    """Greedy per-role allocation by priority order."""

    def _find_best_fit(self, role_name: str, blueprint: Dict, available_pool: List[Drive], target_set: str) -> Dict:
        set_shapes = self.sets_db[target_set]["shapes"]
        extra_shapes = blueprint["extra_pieces"]

        used_indices = set()
        assigned_set, assigned_extra, total_score = [], [], 0.0

        for req_shape in set_shapes:
            best_drive, best_idx, highest_score = None, -1, -1.0
            for idx, drive in enumerate(available_pool):
                if idx in used_indices:
                    continue
                if drive.shape_id == req_shape:
                    score = drive.role_scores.get(role_name, 0.0)
                    if score > highest_score:
                        highest_score, best_drive, best_idx = score, drive, idx
            if best_drive:
                assigned_set.append(best_drive)
                total_score += highest_score
                used_indices.add(best_idx)
            else:
                return {"valid": False, "score": 0.0}

        for req_shape in extra_shapes:
            best_drive, best_idx, highest_score = None, -1, -1.0
            for idx, drive in enumerate(available_pool):
                if idx in used_indices:
                    continue
                if drive.shape_id == req_shape:
                    score = drive.role_scores.get(role_name, 0.0)
                    if score > highest_score:
                        highest_score, best_drive, best_idx = score, drive, idx
            if best_drive:
                assigned_extra.append(best_drive)
                total_score += highest_score
                used_indices.add(best_idx)
            else:
                return {"valid": False, "score": 0.0}

        return {"valid": True, "blueprint": blueprint, "assigned_set_drives": assigned_set,
                "assigned_extra_drives": assigned_extra, "score": round(total_score, 2)}

    def execute(self, candidate_pool: Dict[str, Any], priority_list: List[str], custom_sets: Dict[str, str]) -> Dict[str, Any]:
        logger.info("启动分配模式: 角色优先")

        drives_pool = list(candidate_pool.get("drives", []))
        tapes_pool = candidate_pool.get("tapes", {})
        assigned_tapes = self._pre_allocate_tapes(priority_list, custom_sets, tapes_pool)
        final_allocation = {}

        for role_name in priority_list:
            blueprints = self.blueprints_db.get(role_name, [])
            target_set = self._target_set(role_name, custom_sets)
            logger.info(f"  [{role_name}] 匹配中... (图纸数: {len(blueprints)}, 候选池: {len(drives_pool)})")

            role_tape = assigned_tapes.get(role_name)
            tape_score = role_tape.role_scores.get(role_name, 0.0) if role_tape else 0.0

            best_plan = {"valid": False, "score": -1.0}

            for bp in blueprints:
                plan = self._find_best_fit(role_name, bp, drives_pool, target_set)
                if plan["valid"]:
                    total_score = plan["score"] + tape_score
                    if total_score > best_plan["score"]:
                        plan["score"] = total_score
                        plan["assigned_tape"] = role_tape
                        best_plan = plan

            if best_plan["valid"]:
                final_allocation[role_name] = best_plan
                used_uids = set(d.uid for d in best_plan["assigned_set_drives"]) | set(d.uid for d in best_plan["assigned_extra_drives"])
                drives_pool = [d for d in drives_pool if d.uid not in used_uids]
            else:
                final_allocation[role_name] = {"valid": False}

        return final_allocation

class MatrixBaseStrategy(BaseDispatchStrategy):
    """Shared helpers for matrix-based allocation strategies."""

    MAX_COMBO_LIMIT = 200

    def _build_matrix_environment(self, priority_list):
        role_blueprints_list, valid_roles = [], []
        for role in priority_list:
            bps = self.blueprints_db.get(role, [])
            if bps:
                role_blueprints_list.append(bps)
                valid_roles.append(role)
            else:
                logger.warning(f"角色 [{role}] 没有合法图纸，跳过分配。")
        return role_blueprints_list, valid_roles

    def _iter_bp_combos(self, role_bps_list):
        total = 1
        for bps in role_bps_list:
            total *= len(bps)
        if total <= self.MAX_COMBO_LIMIT:
            yield from itertools.product(*role_bps_list)
        else:
            logger.info(f"图纸组合数 {total} 过大，采样前 {self.MAX_COMBO_LIMIT} 组...")
            count = 0
            for combo in itertools.product(*role_bps_list):
                yield combo
                count += 1
                if count >= self.MAX_COMBO_LIMIT:
                    break

    def _build_profit_matrix(self, bp_combo, valid_roles, drives_pool, custom_sets):
        slots = []
        for role_idx, role in enumerate(valid_roles):
            bp = bp_combo[role_idx]
            target_set = self._target_set(role, custom_sets)
            for shape in self.sets_db[target_set]["shapes"]:
                slots.append({"role": role, "type": "set", "shape": shape, "set_name": target_set, "bp": bp})
            for shape in bp["extra_pieces"]:
                slots.append({"role": role, "type": "extra", "shape": shape, "set_name": None, "bp": bp})

        if len(drives_pool) < len(slots): return None, None

        profit_matrix = np.zeros((len(slots), len(drives_pool)))
        for i, slot in enumerate(slots):
            for j, drive in enumerate(drives_pool):
                if drive.shape_id != slot["shape"]:
                    profit_matrix[i, j] = -10000.0
                else:
                    profit_matrix[i, j] = drive.role_scores.get(slot["role"], 0.0)

        return slots, profit_matrix

    def _init_temp_alloc(self, valid_roles, assigned_tapes):
        return {r: {
            "valid": True,
            "blueprint": None,
            "assigned_tape": assigned_tapes.get(r),
            "assigned_set_drives": [],
            "assigned_extra_drives": [],
            "score": assigned_tapes.get(r).role_scores.get(r, 0.0) if assigned_tapes.get(r) else 0.0
        } for r in valid_roles}

class DrivePriorityStrategy(MatrixBaseStrategy):
    """Greedy best-drive-first allocation via profit matrix."""
    def execute(self, candidate_pool: Dict[str, Any], priority_list: List[str], custom_sets: Dict[str, str]) -> Dict[str, Any]:
        logger.info("启动分配模式: 驱动优先")
        drives_pool = candidate_pool.get("drives", [])
        assigned_tapes = self._pre_allocate_tapes(priority_list, custom_sets, candidate_pool.get("tapes", {}))
        role_bps_list, valid_roles = self._build_matrix_environment(priority_list)
        if not valid_roles: return {}

        best_team_score, best_allocation = -1.0, {}
        combo_count = 0

        for bp_combo in self._iter_bp_combos(role_bps_list):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  驱动优先: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix = self._build_profit_matrix(bp_combo, valid_roles, drives_pool, custom_sets)
            if slots is None: continue

            work_matrix = np.copy(profit_matrix)
            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())
            pick_order = 1

            for _ in range(len(slots)):
                max_val = np.max(work_matrix)
                if max_val < 0:
                    is_valid = False
                    break

                r_idx, c_idx = np.unravel_index(np.argmax(work_matrix), work_matrix.shape)
                slot, drive = slots[r_idx], copy.deepcopy(drives_pool[c_idx])
                role = slot["role"]

                drive.is_mvp = True
                drive.pick_order = pick_order
                pick_order += 1

                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set": temp_alloc[role]["assigned_set_drives"].append(drive)
                else: temp_alloc[role]["assigned_extra_drives"].append(drive)

                temp_alloc[role]["score"] += max_val
                team_score += max_val
                work_matrix[r_idx, :] = -10000.0
                work_matrix[:, c_idx] = -10000.0

            if is_valid and team_score > best_team_score:
                best_team_score, best_allocation = team_score, temp_alloc

        logger.info(f"  驱动优先: 评估完毕，共 {combo_count} 组。")
        return best_allocation

class GlobalOptimalStrategy(MatrixBaseStrategy):
    """Optimal allocation via Hungarian algorithm."""
    def execute(self, candidate_pool: Dict[str, Any], priority_list: List[str], custom_sets: Dict[str, str]) -> Dict[str, Any]:
        logger.info("启动分配模式: 全局最优 (匈牙利算法)")
        drives_pool = candidate_pool.get("drives", [])
        assigned_tapes = self._pre_allocate_tapes(priority_list, custom_sets, candidate_pool.get("tapes", {}))
        role_bps_list, valid_roles = self._build_matrix_environment(priority_list)
        if not valid_roles: return {}

        best_team_score, best_allocation = -1.0, {}
        combo_count = 0

        for bp_combo in self._iter_bp_combos(role_bps_list):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  全局最优: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix = self._build_profit_matrix(bp_combo, valid_roles, drives_pool, custom_sets)
            if slots is None: continue

            cost_matrix = -profit_matrix
            row_ind, col_ind = linear_sum_assignment(cost_matrix)

            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())

            for r_idx, c_idx in zip(row_ind, col_ind):
                profit = profit_matrix[r_idx, c_idx]
                if profit < 0:
                    is_valid = False
                    break

                slot, drive = slots[r_idx], drives_pool[c_idx]
                role = slot["role"]

                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set": temp_alloc[role]["assigned_set_drives"].append(drive)
                else: temp_alloc[role]["assigned_extra_drives"].append(drive)

                temp_alloc[role]["score"] += profit
                team_score += profit

            if is_valid and team_score > best_team_score:
                best_team_score, best_allocation = team_score, temp_alloc

        logger.info(f"  全局最优: 评估完毕，共 {combo_count} 组。")
        return best_allocation
