# 分配策略共用的槽位矩阵和临时结果构建能力。
import numpy as np
from typing import List, Dict

from src.optimizer.blueprint_candidate_builder import BlueprintCandidateBuilder
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap


class PreparedProfitMatrix:
    """仅在一次固定候选池/偏好内复用评分行，每个预留分支新建实例。

    列顺序和排除 UID 不跨分支复用；只缓存不随已选装备改变的排名，
    依赖当前暴击率的选择仍走原贪心路径，profit 与隐藏排名分保持分离。
    """

    def __init__(self, strategy, drives_pool, crit_priority_modes):
        self._strategy = strategy
        self._drives = tuple(drives_pool)
        self._preferences = crit_priority_modes or {}
        self._shape_indices = {}
        for index, drive in enumerate(self._drives):
            self._shape_indices.setdefault(drive.shape_id, []).append(index)
        self._rows = {}

    def _row(self, role, shape, uses_bonus):
        key = (role, shape, uses_bonus)
        if key not in self._rows:
            profit = np.full(len(self._drives), -10000.0)
            ranking = np.full(len(self._drives), -10000.0)
            config = self._preferences.get(role)
            for index in self._shape_indices.get(shape, ()):
                drive = self._drives[index]
                if not self._strategy._item_allowed_for_role(drive, config):
                    continue
                score = drive.role_scores.get(role, 0.0)
                profit[index] = score
                ranking[index] = self._strategy._rank_score_for_drive(
                    role, drive, score, config,
                    include_extra_shape_bonus=uses_bonus,
                )
            self._rows[key] = profit, ranking
        return self._rows[key]

    def build(self, bp_combo, valid_roles, custom_sets, *, include_extra_shape_bonus=True):
        slots = self._strategy._build_group_slots(bp_combo, valid_roles, custom_sets)
        if len(self._drives) < len(slots):
            return None, None, None
        profit_matrix = np.empty((len(slots), len(self._drives)))
        ranking_matrix = np.empty_like(profit_matrix)
        for index, slot in enumerate(slots):
            uses_bonus = self._strategy._slot_uses_extra_shape_bonus(
                slot["type"], slot.get("bp"), include_extra_shape_bonus,
            )
            profit, ranking = self._row(slot["role"], slot["shape"], uses_bonus)
            # 拷入独立矩阵，后续求解修改不能污染缓存行。
            profit_matrix[index] = profit
            ranking_matrix[index] = ranking
        return slots, profit_matrix, ranking_matrix


class AllocationMatrixBuilder(BlueprintCandidateBuilder):
    def _prepare_profit_matrix(self, drives_pool, crit_priority_modes=None):
        return PreparedProfitMatrix(self, drives_pool, crit_priority_modes)

    def _build_group_slots(self, bp_combo, valid_roles, custom_sets):
        slots = []
        for role_idx, role in enumerate(valid_roles):
            bp = bp_combo[role_idx]
            target_set = self._target_set(role, custom_sets)
            for shape in self._set_pieces_for_blueprint(bp, target_set):
                slots.append({"role": role, "type": "set", "shape": shape, "set_name": target_set, "bp": bp})
            for shape in bp["extra_pieces"]:
                slots.append({"role": role, "type": "extra", "shape": shape, "set_name": None, "bp": bp})
        return slots

    def _build_profit_matrix(
        self,
        bp_combo,
        valid_roles,
        drives_pool,
        custom_sets,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
        return self._prepare_profit_matrix(drives_pool, crit_priority_modes).build(
            bp_combo, valid_roles, custom_sets,
            include_extra_shape_bonus=include_extra_shape_bonus,
        )

    def _init_temp_alloc(self, valid_roles, assigned_tapes):
        return {r: {
            "valid": True,
            "blueprint": None,
            "assigned_tape": assigned_tapes.get(r),
            "assigned_set_drives": [],
            "assigned_extra_drives": [],
            "score": assigned_tapes.get(r).role_scores.get(r, 0.0) if assigned_tapes.get(r) else 0.0
        } for r in valid_roles}

    def execute(self, candidate_pool: CandidatePool, priority_list: List[str], custom_sets: CustomSetMap,
                crit_priority_modes: StatPriorityConfigMap = None,
                priority_groups: list[list[str]] | None = None,
                crit_rate_caps: Dict[str, float] | None = None) -> AllocationResult:
        raise NotImplementedError
