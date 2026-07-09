# 分配策略共用的槽位矩阵和临时结果构建能力。
import numpy as np
from typing import List, Dict

from src.models.equipment import Drive
from src.optimizer.blueprint_candidate_builder import BlueprintCandidateBuilder
from src.optimizer.contracts import AllocationResult, CandidatePool, CustomSetMap, StatPriorityConfigMap


class AllocationMatrixBuilder(BlueprintCandidateBuilder):
    def _build_profit_matrix(
        self,
        bp_combo,
        valid_roles,
        drives_pool,
        custom_sets,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
        crit_priority_modes = crit_priority_modes or {}
        slots = []
        for role_idx, role in enumerate(valid_roles):
            bp = bp_combo[role_idx]
            target_set = self._target_set(role, custom_sets)
            for shape in self._set_pieces_for_blueprint(bp, target_set):
                slots.append({"role": role, "type": "set", "shape": shape, "set_name": target_set, "bp": bp})
            for shape in bp["extra_pieces"]:
                slots.append({"role": role, "type": "extra", "shape": shape, "set_name": None, "bp": bp})

        if len(drives_pool) < len(slots): return None, None, None

        profit_matrix = np.zeros((len(slots), len(drives_pool)))
        ranking_matrix = np.zeros((len(slots), len(drives_pool)))
        for i, slot in enumerate(slots):
            for j, drive in enumerate(drives_pool):
                if drive.shape_id != slot["shape"]:
                    profit_matrix[i, j] = -10000.0
                    ranking_matrix[i, j] = -10000.0
                else:
                    score = drive.role_scores.get(slot["role"], 0.0)
                    profit_matrix[i, j] = score
                    slot_uses_bonus = self._slot_uses_extra_shape_bonus(
                        slot["type"],
                        slot.get("bp"),
                        include_extra_shape_bonus,
                    )
                    ranking_matrix[i, j] = self._rank_score_for_drive(
                        slot["role"],
                        drive,
                        score,
                        crit_priority_modes.get(slot["role"]),
                        include_extra_shape_bonus=slot_uses_bonus,
                    )

        return slots, profit_matrix, ranking_matrix

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

