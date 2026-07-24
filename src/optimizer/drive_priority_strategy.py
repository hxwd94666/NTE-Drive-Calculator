# 基于矩阵的驱动优先和全局最优分配策略。
import copy
import numpy as np
from scipy.optimize import linear_sum_assignment
from typing import List

from src.optimizer.allocation_matrix_builder import AllocationMatrixBuilder
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap
from src.utils.logger import logger

class MatrixBaseStrategy(AllocationMatrixBuilder):
    """Shared helpers for matrix-based allocation strategies."""

    def _build_matrix_environment(self, priority_list):
        role_blueprints_list, valid_roles = [], []
        for role in priority_list:
            raw_bps = self.blueprints_db.get(role, [])
            bps = self._dedupe_blueprints_by_extra_pieces(raw_bps)
            if bps:
                if len(bps) < len(raw_bps):
                    logger.info(f"角色 [{role}] 图纸形状组合去重: {len(raw_bps)} -> {len(bps)}")
                role_blueprints_list.append(bps)
                valid_roles.append(role)
            else:
                logger.warning(f"角色 [{role}] 没有合法图纸，跳过分配。")
        return role_blueprints_list, valid_roles

class DrivePriorityStrategy(MatrixBaseStrategy):
    """Greedy best-drive-first allocation via profit matrix."""
    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None) -> AllocationResult:
        logger.info("启动分配模式: 驱动优先")
        drives_pool = candidate_pool.get("drives", [])
        assigned_tapes = self._pre_allocate_tapes_optimal(priority_list, custom_sets, candidate_pool.get("tapes", {}))
        role_bps_list, valid_roles = self._build_matrix_environment(priority_list)
        if not valid_roles: return {}

        best_team_score, best_team_rank_score, best_allocation = -1.0, -1.0, {}
        best_priority_hits = -1
        combo_count = 0

        for bp_combo in self._iter_bp_combos(role_bps_list, valid_roles, drives_pool, custom_sets, crit_priority_modes):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  驱动优先: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix, ranking_matrix = self._build_profit_matrix(
                bp_combo, valid_roles, drives_pool, custom_sets, crit_priority_modes
            )
            if slots is None: continue

            work_matrix = np.copy(ranking_matrix)
            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())
            team_rank_score = team_score
            priority_hits = 0
            pick_order = 1

            for _ in range(len(slots)):
                max_val = np.max(work_matrix)
                if max_val < 0:
                    is_valid = False
                    break

                r_idx, c_idx = np.unravel_index(np.argmax(work_matrix), work_matrix.shape)
                slot, drive = slots[r_idx], copy.deepcopy(drives_pool[c_idx])
                role = slot["role"]
                real_score = profit_matrix[r_idx, c_idx]
                rank_score = ranking_matrix[r_idx, c_idx]

                drive.is_mvp = True
                drive.pick_order = pick_order
                pick_order += 1

                temp_alloc[role]["blueprint"] = slot["bp"]
                if slot["type"] == "set": temp_alloc[role]["assigned_set_drives"].append(drive)
                else: temp_alloc[role]["assigned_extra_drives"].append(drive)

                temp_alloc[role]["score"] += real_score
                team_score += real_score
                team_rank_score += rank_score
                priority_hits += self._stat_priority_hit_count(
                    role, drive, crit_priority_modes.get(role)
                )
                work_matrix[r_idx, :] = -10000.0
                work_matrix[:, c_idx] = -10000.0

            if is_valid and (priority_hits, team_rank_score, team_score) > (
                best_priority_hits,
                best_team_rank_score,
                best_team_score,
            ):
                best_priority_hits = priority_hits
                best_team_rank_score = team_rank_score
                best_team_score, best_allocation = team_score, temp_alloc

        logger.info(f"  驱动优先: 评估完毕，共 {combo_count} 组。")
        return best_allocation

class GlobalOptimalStrategy(MatrixBaseStrategy):
    """Optimal allocation via Hungarian algorithm."""
    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None) -> AllocationResult:
        logger.info("启动分配模式: 全局最优 (匈牙利算法)")
        drives_pool = candidate_pool.get("drives", [])
        assigned_tapes = self._pre_allocate_tapes_optimal(priority_list, custom_sets, candidate_pool.get("tapes", {}))
        role_bps_list, valid_roles = self._build_matrix_environment(priority_list)
        if not valid_roles: return {}

        best_team_score, best_team_rank_score, best_allocation = -1.0, -1.0, {}
        best_priority_hits = -1
        combo_count = 0

        for bp_combo in self._iter_bp_combos(
            role_bps_list,
            valid_roles,
            drives_pool,
            custom_sets,
            crit_priority_modes,
        ):
            combo_count += 1
            if combo_count % 50 == 0:
                logger.info(f"  全局最优: 已评估 {combo_count} 组图纸组合...")

            slots, profit_matrix, ranking_matrix = self._build_profit_matrix(
                bp_combo,
                valid_roles,
                drives_pool,
                custom_sets,
                crit_priority_modes,
            )
            if slots is None: continue

            cost_matrix = -ranking_matrix
            row_ind, col_ind = linear_sum_assignment(cost_matrix)

            is_valid = True
            temp_alloc = self._init_temp_alloc(valid_roles, assigned_tapes)
            team_score = sum(alloc["score"] for alloc in temp_alloc.values())
            team_rank_score = team_score
            priority_hits = 0

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
                team_rank_score += ranking_matrix[r_idx, c_idx]
                priority_hits += self._stat_priority_hit_count(
                    role, drive, crit_priority_modes.get(role)
                )

            if is_valid and (priority_hits, team_rank_score, team_score) > (
                best_priority_hits,
                best_team_rank_score,
                best_team_score,
            ):
                best_priority_hits = priority_hits
                best_team_score, best_team_rank_score, best_allocation = (
                    team_score,
                    team_rank_score,
                    temp_alloc,
                )

        logger.info(f"  全局最优: 评估完毕，共 {combo_count} 组。")
        return best_allocation
