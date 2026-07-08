# 图纸候选评分、组合去重和大组合数量限制的构建能力。
import heapq
import itertools

from src.optimizer.drive_candidate_ranker import BaseDispatchStrategy
from src.solver.blueprint_utils import blueprint_piece_signature, dedupe_blueprints_by_piece_signature
from src.utils.logger import logger


class BlueprintCandidateBuilder(BaseDispatchStrategy):
    def _blueprint_extra_key(self, blueprint):
        return blueprint_piece_signature(blueprint)

    def _dedupe_blueprints_by_extra_pieces(self, blueprints):
        return dedupe_blueprints_by_piece_signature(blueprints)

    def _shape_score_buckets(self, role, drives_pool, crit_config=None, include_extra_shape_bonus: bool = True):
        buckets = {}
        for drive in drives_pool or []:
            base_score = drive.role_scores.get(role, 0.0)
            rank_score = self._rank_score_for_drive(
                role,
                drive,
                base_score,
                crit_config,
                include_extra_shape_bonus=include_extra_shape_bonus,
            )
            buckets.setdefault(drive.shape_id, []).append(rank_score)
        for scores in buckets.values():
            scores.sort(reverse=True)
        return buckets

    def _blueprint_theoretical_score(
        self,
        role,
        blueprint,
        drives_pool,
        custom_sets,
        crit_config=None,
        include_extra_shape_bonus: bool = True,
    ):
        target_set = self._target_set(role, custom_sets)
        set_uses_bonus = self._slot_uses_extra_shape_bonus("set", blueprint, include_extra_shape_bonus)
        set_buckets = self._shape_score_buckets(
            role,
            drives_pool,
            crit_config,
            include_extra_shape_bonus=set_uses_bonus,
        )
        extra_buckets = self._shape_score_buckets(
            role,
            drives_pool,
            crit_config,
            include_extra_shape_bonus=False,
        )
        used_counts = {}
        total = 0.0
        required_slots = [
            ("set", shape) for shape in self._set_pieces_for_blueprint(blueprint, target_set)
        ] + [
            ("extra", shape) for shape in list(blueprint.get("extra_pieces", []))
        ]
        for slot_type, shape in required_slots:
            bucket_key = (slot_type, shape)
            buckets = set_buckets if slot_type == "set" else extra_buckets
            used = used_counts.get(bucket_key, 0)
            scores = buckets.get(shape, [])
            if used >= len(scores):
                return -10000.0
            total += scores[used]
            used_counts[bucket_key] = used + 1
        return total

    def _rank_role_blueprints(
        self,
        role_bps_list,
        valid_roles,
        drives_pool,
        custom_sets,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
        crit_priority_modes = crit_priority_modes or {}
        ranked = []
        for role, bps in zip(valid_roles, role_bps_list):
            role_ranked = [
                (
                    self._blueprint_theoretical_score(
                        role,
                        bp,
                        drives_pool,
                        custom_sets,
                        crit_priority_modes.get(role),
                        include_extra_shape_bonus=include_extra_shape_bonus,
                    ),
                    index,
                    bp,
                )
                for index, bp in enumerate(bps)
            ]
            role_ranked.sort(key=lambda item: (-item[0], item[1]))
            ranked.append(role_ranked)
        return ranked

    def _iter_ranked_bp_combos(self, ranked_role_bps):
        if not ranked_role_bps or any(not bps for bps in ranked_role_bps):
            return

        start = tuple(0 for _ in ranked_role_bps)

        def score_for(indexes):
            return sum(ranked_role_bps[role_idx][bp_idx][0] for role_idx, bp_idx in enumerate(indexes))

        seen = {start}
        heap = [(-score_for(start), start)]
        count = 0

        while heap and count < self.MAX_COMBO_LIMIT:
            _, indexes = heapq.heappop(heap)
            yield tuple(ranked_role_bps[role_idx][bp_idx][2] for role_idx, bp_idx in enumerate(indexes))
            count += 1

            for role_idx in range(len(indexes)):
                next_indexes = list(indexes)
                next_indexes[role_idx] += 1
                if next_indexes[role_idx] >= len(ranked_role_bps[role_idx]):
                    continue
                next_indexes = tuple(next_indexes)
                if next_indexes in seen:
                    continue
                seen.add(next_indexes)
                heapq.heappush(heap, (-score_for(next_indexes), next_indexes))

    def _iter_bp_combos(
        self,
        role_bps_list,
        valid_roles=None,
        drives_pool=None,
        custom_sets=None,
        crit_priority_modes=None,
        include_extra_shape_bonus: bool = True,
    ):
        total = 1
        for bps in role_bps_list:
            total *= len(bps)
        if total <= self.MAX_COMBO_LIMIT:
            yield from itertools.product(*role_bps_list)
        else:
            logger.info(f"图纸组合数 {total} 过大，按理论上限筛选前 {self.MAX_COMBO_LIMIT} 组...")
            if not valid_roles or drives_pool is None:
                count = 0
                for combo in itertools.product(*role_bps_list):
                    yield combo
                    count += 1
                    if count >= self.MAX_COMBO_LIMIT:
                        break
                return

            ranked_role_bps = self._rank_role_blueprints(
                role_bps_list,
                valid_roles,
                drives_pool,
                custom_sets or {},
                crit_priority_modes,
                include_extra_shape_bonus=include_extra_shape_bonus,
            )
            yield from self._iter_ranked_bp_combos(ranked_role_bps)

