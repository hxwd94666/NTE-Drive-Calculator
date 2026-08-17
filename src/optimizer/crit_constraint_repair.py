# 同级组暴击约束失败后的有界全图纸重配。
"""Bounded repair for failed equal-priority critical-rate plans.

Every semantic blueprint is checked with a cheap critical-rate envelope.  Only
feasible blueprint/tape pairs enter the bounded equipment search, and the
normal quality, blacklist, suit and stat-priority rules remain in force.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any, Protocol, TypedDict

from src.domain.crit_threshold import (
    meets_preference_grade_limit,
    preference_config_active,
)
from src.models.equipment import Drive, Tape
from src.optimizer.contracts import AllocationResult


class _DriveState(TypedDict):
    drives: list[Drive]
    uids: set[str]
    score: float
    typed: list[tuple[str, Drive]]


class _JointState(TypedDict):
    plans: dict[str, dict | None]
    drive_uids: set[str]
    tape_uids: set[str]
    valid_count: int
    score: float


if TYPE_CHECKING:
    class _CritConstraintRepairBase(Protocol):
        blueprints_db: dict[str, list[dict]]

        def _item_allowed_for_role(self, item: Drive | Tape, config: dict | None) -> bool: ...
        def _tape_matches_core_target(self, role: str, tape: Tape, custom_sets: dict[str, str]) -> bool: ...
        def _is_crit_rate_key(self, key: str) -> bool: ...
        def _current_role_crit(self, role: str, tape: Tape | None, drives: list[Drive]) -> float: ...
        def _crit_floor_threshold(self, config: dict | None) -> float | None: ...
        def _crit_rate_cap(self, role: str, crit_rate_caps: dict[str, float] | None) -> float | None: ...
        def _target_set(self, role: str, custom_sets: dict[str, str]) -> str: ...
        def _set_pieces_for_blueprint(self, blueprint: dict, target_set: str) -> list[str]: ...
        def _stat_priority_key_for_items(
            self, role: str, items: list[Drive], config: dict | None,
        ) -> tuple: ...
        def _stat_priority_depth(
            self, role: str, item: Drive | Tape, config: dict | None,
        ) -> int: ...
else:
    class _CritConstraintRepairBase:
        pass


class CritConstraintRepairMixin(_CritConstraintRepairBase):
    REPAIR_SCORE_CANDIDATES = 6
    REPAIR_CRIT_CANDIDATES = 4
    REPAIR_TAPE_CANDIDATES = 6
    REPAIR_BEAM_WIDTH = 64
    REPAIR_MAX_EXPANSIONS = 6000
    REPAIR_ROLE_PLAN_LIMIT = 12

    def _repair_blueprints(self, role: str) -> list[dict]:
        """Return every semantically distinct blueprint for one role."""

        unique: list[dict] = []
        seen: set[tuple[Any, ...]] = set()
        for blueprint in self.blueprints_db.get(role, []) or ():
            signature = (
                str(blueprint.get("set_effect_mode") or ""),
                tuple(sorted(Counter(str(v) for v in blueprint.get("set_pieces", ()) or ()).items())),
                tuple(sorted(Counter(str(v) for v in blueprint.get("extra_pieces", ()) or ()).items())),
            )
            if signature in seen:
                continue
            seen.add(signature)
            unique.append(blueprint)
        return unique

    def _repair_quality_allowed(self, role: str, item: Drive | Tape, config: dict | None) -> bool:
        if not self._item_allowed_for_role(item, config):
            return False
        if not preference_config_active(config):
            return True
        return meets_preference_grade_limit(
            float(item.role_scores.get(role, 0.0)),
            int(item.area or 1),
            config,
        )

    def _repair_tape_candidates(
        self,
        role: str,
        tapes_pool: dict[str, list[Tape]],
        assigned_tapes: dict[str, Tape | None],
        unavailable_tape_uids: set[str],
        custom_sets: dict[str, str],
        config: dict | None,
    ) -> list[Tape | None]:
        primary = assigned_tapes.get(role)
        legal: list[Tape] = []
        for tape in tapes_pool.get(role, ()):
            if (
                tape.uid in unavailable_tape_uids
                or not self._tape_matches_core_target(role, tape, custom_sets)
                or not self._repair_quality_allowed(role, tape, config)
            ):
                continue
            legal.append(tape)
        if (
            isinstance(primary, Tape)
            and primary.uid not in unavailable_tape_uids
            and self._tape_matches_core_target(role, primary, custom_sets)
            and self._repair_quality_allowed(role, primary, config)
            and all(tape.uid != primary.uid for tape in legal)
        ):
            legal.append(primary)

        # Keep the highest-score cards overall and the best representatives of
        # each main stat.  This preserves non-critical alternatives without
        # letting a low-score card enter merely because of its main stat.
        legal.sort(
            key=lambda tape: (
                self._stat_priority_depth(role, tape, config),
                float(tape.role_scores.get(role, 0.0)),
            ),
            reverse=True,
        )
        selected: dict[str, Tape] = {}
        best_by_depth: dict[int, Tape] = {}
        for tape in legal:
            best_by_depth.setdefault(
                self._stat_priority_depth(role, tape, config),
                tape,
            )
        for depth in sorted(best_by_depth, reverse=True):
            tape = best_by_depth[depth]
            selected.setdefault(tape.uid, tape)
        if isinstance(primary, Tape):
            for tape in legal:
                if tape.uid == primary.uid:
                    selected.setdefault(tape.uid, tape)
                    break
        best_crit = next((tape for tape in legal if self._is_crit_rate_key(tape.main_stats)), None)
        best_noncrit = next((tape for tape in legal if not self._is_crit_rate_key(tape.main_stats)), None)
        for tape in (best_crit, best_noncrit):
            if isinstance(tape, Tape):
                selected.setdefault(tape.uid, tape)
        for tape in legal:
            main_key = str(tape.main_stats or "")
            if any(str(existing.main_stats or "") == main_key for existing in selected.values()):
                continue
            selected[tape.uid] = tape
            if len(selected) >= self.REPAIR_TAPE_CANDIDATES:
                break
        for tape in legal:
            selected.setdefault(tape.uid, tape)
            if len(selected) >= self.REPAIR_TAPE_CANDIDATES:
                break
        candidate_count = max(self.REPAIR_TAPE_CANDIDATES, len(best_by_depth))
        candidates = list(selected.values())[:candidate_count]
        return candidates or [None]

    def _repair_drive_crit_delta(self, role: str, drive: Drive) -> float:
        empty = self._current_role_crit(role, None, [])
        return self._current_role_crit(role, None, [drive]) - empty

    def _repair_slot_candidates(
        self,
        role: str,
        slot: dict,
        drives: list[Drive],
        available_uids: set[str],
        config: dict | None,
        candidate_limit: int,
    ) -> list[Drive]:
        legal = [
            drive
            for drive in drives
            if drive.uid in available_uids
            and drive.shape_id == slot["shape"]
            and self._repair_quality_allowed(role, drive, config)
        ]
        if not legal:
            return []

        score_sorted = sorted(
            legal,
            key=lambda drive: float(drive.role_scores.get(role, 0.0)),
            reverse=True,
        )
        high_crit = sorted(
            legal,
            key=lambda drive: (
                self._repair_drive_crit_delta(role, drive),
                float(drive.role_scores.get(role, 0.0)),
            ),
            reverse=True,
        )
        low_crit = sorted(
            legal,
            key=lambda drive: (
                self._repair_drive_crit_delta(role, drive),
                -float(drive.role_scores.get(role, 0.0)),
            ),
        )
        selected: dict[str, Drive] = {}
        # Reserve space for all three directions before filling the remaining
        # capacity by score.  A small upstream screen limit must not erase the
        # low-critical lane needed by an upper cap.
        depth_buckets: dict[int, list[Drive]] = {}
        for drive in legal:
            depth_buckets.setdefault(
                self._stat_priority_depth(role, drive, config),
                [],
            ).append(drive)
        for depth in sorted(depth_buckets, reverse=True):
            bucket = sorted(
                depth_buckets[depth],
                key=lambda drive: float(drive.role_scores.get(role, 0.0)),
                reverse=True,
            )
            selected.setdefault(bucket[0].uid, bucket[0])
        for drive in score_sorted[:4] + high_crit[:3] + low_crit[:3]:
            selected.setdefault(drive.uid, drive)
        for drive in (
            score_sorted[: self.REPAIR_SCORE_CANDIDATES]
            + high_crit[: self.REPAIR_CRIT_CANDIDATES]
            + low_crit[: self.REPAIR_CRIT_CANDIDATES]
        ):
            selected.setdefault(drive.uid, drive)
        limit = max(10, min(int(candidate_limit), 14), len(depth_buckets))
        return list(selected.values())[:limit]

    def _repair_bounds(
        self,
        role: str,
        config: dict | None,
        crit_rate_caps: dict[str, float] | None,
    ) -> tuple[float, float]:
        floor = self._crit_floor_threshold(config)
        cap = self._crit_rate_cap(role, crit_rate_caps)
        return (float(floor or 0.0), float(cap) if cap is not None else float("inf"))

    def _repair_blueprint_slots(
        self,
        role: str,
        blueprint: dict,
        custom_sets: dict[str, str],
    ) -> list[dict]:
        target_set = self._target_set(role, custom_sets)
        return [
            {"type": "set", "shape": shape, "bp": blueprint}
            for shape in self._set_pieces_for_blueprint(blueprint, target_set)
        ] + [
            {"type": "extra", "shape": shape, "bp": blueprint}
            for shape in blueprint.get("extra_pieces", ()) or ()
        ]

    def _repair_interval_envelope(
        self,
        role: str,
        tape: Tape | None,
        slots: list[dict],
        candidates_by_slot: list[list[Drive]],
    ) -> tuple[float, float] | None:
        required = Counter(str(slot["shape"]) for slot in slots)
        by_shape: dict[str, dict[str, Drive]] = {}
        for slot, candidates in zip(slots, candidates_by_slot):
            bucket = by_shape.setdefault(str(slot["shape"]), {})
            for drive in candidates:
                bucket.setdefault(drive.uid, drive)
        minimum: list[Drive] = []
        maximum: list[Drive] = []
        for shape, count in required.items():
            values = list(by_shape.get(shape, {}).values())
            if len(values) < count:
                return None
            values.sort(key=lambda drive: self._repair_drive_crit_delta(role, drive))
            minimum.extend(values[:count])
            maximum.extend(values[-count:])
        return (
            self._current_role_crit(role, tape, minimum),
            self._current_role_crit(role, tape, maximum),
        )

    def _trim_repair_states(
        self,
        role: str,
        tape: Tape | None,
        states: list[_DriveState],
        floor: float,
        cap: float,
    ) -> list[_DriveState]:
        best_by_bucket: dict[float, _DriveState] = {}
        for state in states:
            total = self._current_role_crit(role, tape, state["drives"])
            bucket = round(total * 2.0) / 2.0
            previous = best_by_bucket.get(bucket)
            if previous is None or state["score"] > previous["score"]:
                best_by_bucket[bucket] = state
        values = list(best_by_bucket.values())

        def distance(state: _DriveState) -> float:
            total = self._current_role_crit(role, tape, state["drives"])
            if total < floor:
                return floor - total
            if total > cap:
                return total - cap
            return 0.0

        by_score = sorted(values, key=lambda state: state["score"], reverse=True)
        by_distance = sorted(values, key=lambda state: (distance(state), -state["score"]))
        selected: dict[tuple[str, ...], _DriveState] = {}
        half = max(1, self.REPAIR_BEAM_WIDTH // 2)
        for state in by_score[:half] + by_distance[:half]:
            signature = tuple(sorted(state["uids"]))
            selected.setdefault(signature, state)
        return list(selected.values())[: self.REPAIR_BEAM_WIDTH]

    def _search_repair_spec(
        self,
        role: str,
        blueprint: dict,
        tape: Tape | None,
        slots: list[dict],
        candidates_by_slot: list[list[Drive]],
        config: dict | None,
        floor: float,
        cap: float,
        expansion_budget: int,
    ) -> tuple[list[dict], int, list[float]]:
        states: list[_DriveState] = [
            {"drives": [], "uids": set(), "score": 0.0, "typed": []}
        ]
        expansions = 0
        for slot_index, slot in enumerate(slots):
            next_states: list[_DriveState] = []
            remaining = candidates_by_slot[slot_index + 1 :]
            optimistic_remaining = sum(
                max((self._repair_drive_crit_delta(role, drive) for drive in candidates), default=0.0)
                for candidates in remaining
            )
            for state in states:
                for drive in candidates_by_slot[slot_index]:
                    if drive.uid in state["uids"]:
                        continue
                    expansions += 1
                    if expansions > expansion_budget:
                        break
                    drives = [*state["drives"], drive]
                    current = self._current_role_crit(role, tape, drives)
                    if current > cap + 1e-9:
                        continue
                    if current + optimistic_remaining + 1e-9 < floor:
                        continue
                    next_states.append(
                        {
                            "drives": drives,
                            "uids": {*state["uids"], drive.uid},
                            "score": state["score"] + float(drive.role_scores.get(role, 0.0)),
                            "typed": [*state["typed"], (slot["type"], drive)],
                        }
                    )
                if expansions > expansion_budget:
                    break
            if not next_states:
                return [], expansions, []
            states = self._trim_repair_states(role, tape, next_states, floor, cap)

        totals = [self._current_role_crit(role, tape, state["drives"]) for state in states]
        valid_states = [
            state
            for state, total in zip(states, totals)
            if total + 1e-9 >= floor and total <= cap + 1e-9
        ]
        valid_states.sort(key=lambda state: float(state["score"]), reverse=True)
        plans: list[dict] = []
        tape_score = float(tape.role_scores.get(role, 0.0)) if tape else 0.0
        for state in valid_states[:3]:
            set_drives = [drive for slot_type, drive in state["typed"] if slot_type == "set"]
            extra_drives = [drive for slot_type, drive in state["typed"] if slot_type == "extra"]
            plans.append(
                {
                    "valid": True,
                    "blueprint": blueprint,
                    "assigned_tape": tape,
                    "assigned_set_drives": set_drives,
                    "assigned_extra_drives": extra_drives,
                    "score": round(tape_score + state["score"], 2),
                    "stat_priority_key": self._stat_priority_key_for_items(
                        role, state["drives"], config,
                    ),
                }
            )
        return plans, expansions, totals

    def _repair_failure_reason(
        self,
        floor: float,
        cap: float,
        observed: list[float],
    ) -> str:
        if not observed:
            if floor > 0 and cap == float("inf"):
                return (
                    f"没有达成暴击率最小值 {floor:g}% 的方案"
                    "（本次从零重配没有高质量完整方案）"
                )
            if floor <= 0 and cap < float("inf"):
                return (
                    f"没有满足暴击率上限 {cap:g}% 的方案"
                    "（本次从零重配没有高质量完整方案）"
                )
            return (
                f"没有同时满足暴击率区间 {floor:g}%～{cap:g}% 的高质量方案"
                "（本次从零重配没有高质量完整方案）"
            )
        highest = max(observed)
        lowest = min(observed)
        if highest + 1e-9 < floor:
            return f"没有达成暴击率最小值 {floor:g}% 的方案（本次从零重配最高达到 {highest:g}%）"
        if lowest > cap + 1e-9:
            return f"没有满足暴击率上限 {cap:g}% 的方案（本次从零重配最低为 {lowest:g}%）"
        return (
            f"没有同时满足暴击率区间 {floor:g}%～{cap:g}% 的高质量方案"
            f"（本次从零重配范围 {lowest:g}%～{highest:g}%）"
        )

    def _repair_role_plans(
        self,
        role: str,
        full_drives: list[Drive],
        available_drive_uids: set[str],
        tapes_pool: dict[str, list[Tape]],
        assigned_tapes: dict[str, Tape | None],
        unavailable_tape_uids: set[str],
        custom_sets: dict[str, str],
        config: dict | None,
        crit_rate_caps: dict[str, float] | None,
        candidate_limit: int,
    ) -> tuple[list[dict], str]:
        floor, cap = self._repair_bounds(role, config, crit_rate_caps)
        tapes = self._repair_tape_candidates(
            role,
            tapes_pool,
            assigned_tapes,
            unavailable_tape_uids,
            custom_sets,
            config,
        )
        specs: list[tuple[float, dict, Tape | None, list[dict], list[list[Drive]]]] = []
        observed: list[float] = []
        for blueprint in self._repair_blueprints(role):
            slots = self._repair_blueprint_slots(role, blueprint, custom_sets)
            candidates_by_slot = [
                self._repair_slot_candidates(
                    role, slot, full_drives, available_drive_uids, config, candidate_limit,
                )
                for slot in slots
            ]
            if any(not candidates for candidates in candidates_by_slot):
                continue
            theoretical_score = sum(
                max(float(drive.role_scores.get(role, 0.0)) for drive in candidates)
                for candidates in candidates_by_slot
            )
            for tape in tapes:
                envelope = self._repair_interval_envelope(
                    role, tape, slots, candidates_by_slot,
                )
                if envelope is None:
                    continue
                observed.extend(envelope)
                if envelope[1] + 1e-9 < floor or envelope[0] > cap + 1e-9:
                    continue
                tape_score = float(tape.role_scores.get(role, 0.0)) if tape else 0.0
                specs.append((theoretical_score + tape_score, blueprint, tape, slots, candidates_by_slot))

        specs.sort(key=lambda spec: spec[0], reverse=True)
        plans: list[dict] = []
        remaining_budget = self.REPAIR_MAX_EXPANSIONS
        for _, blueprint, tape, slots, candidates_by_slot in specs:
            if remaining_budget <= 0:
                break
            per_spec_budget = min(1500, remaining_budget)
            found, expansions, totals = self._search_repair_spec(
                role,
                blueprint,
                tape,
                slots,
                candidates_by_slot,
                config,
                floor,
                cap,
                per_spec_budget,
            )
            observed.extend(totals)
            plans.extend(found)
            remaining_budget -= max(1, expansions)

        deduped: dict[tuple[Any, ...], dict] = {}
        for plan in plans:
            signature = (
                getattr(plan.get("assigned_tape"), "uid", None),
                tuple(sorted(drive.uid for drive in plan.get("assigned_set_drives", ()) or ())),
                tuple(sorted(drive.uid for drive in plan.get("assigned_extra_drives", ()) or ())),
            )
            previous = deduped.get(signature)
            if previous is None or float(plan.get("score", 0.0)) > float(previous.get("score", 0.0)):
                deduped[signature] = plan
        ranked = sorted(
            deduped.values(),
            key=lambda plan: (
                tuple(plan.get("stat_priority_key", ()) or ()),
                float(plan.get("score", 0.0)),
            ),
            reverse=True,
        )[: self.REPAIR_ROLE_PLAN_LIMIT]
        return ranked, self._repair_failure_reason(floor, cap, observed)

    def _repair_failed_equal_priority_roles(
        self,
        failed_roles: list[str],
        full_drives: list[Drive],
        available_drive_uids: set[str],
        tapes_pool: dict[str, list[Tape]],
        assigned_tapes: dict[str, Tape | None],
        unavailable_tape_uids: set[str],
        custom_sets: dict[str, str],
        crit_priority_modes: dict[str, dict],
        crit_rate_caps: dict[str, float] | None,
        candidate_limit: int,
    ) -> AllocationResult:
        role_plans: dict[str, list[dict]] = {}
        reasons: dict[str, str] = {}
        for role in failed_roles:
            role_plans[role], reasons[role] = self._repair_role_plans(
                role,
                full_drives,
                available_drive_uids,
                tapes_pool,
                assigned_tapes,
                unavailable_tape_uids,
                custom_sets,
                crit_priority_modes.get(role),
                crit_rate_caps,
                candidate_limit,
            )

        states: list[_JointState] = [
            {"plans": {}, "drive_uids": set(), "tape_uids": set(), "valid_count": 0, "score": 0.0}
        ]
        for role in failed_roles:
            next_states: list[_JointState] = []
            options: list[dict | None] = [*role_plans.get(role, ()), None]
            for state in states:
                for plan in options:
                    if plan is None:
                        next_states.append({**state, "plans": {**state["plans"], role: None}})
                        continue
                    drive_uids = {
                        drive.uid
                        for drive in (
                            *(plan.get("assigned_set_drives", ()) or ()),
                            *(plan.get("assigned_extra_drives", ()) or ()),
                        )
                    }
                    tape = plan.get("assigned_tape")
                    tape_uids = {tape.uid} if isinstance(tape, Tape) else set()
                    if drive_uids & state["drive_uids"] or tape_uids & state["tape_uids"]:
                        continue
                    next_states.append(
                        {
                            "plans": {**state["plans"], role: plan},
                            "drive_uids": state["drive_uids"] | drive_uids,
                            "tape_uids": state["tape_uids"] | tape_uids,
                            "valid_count": state["valid_count"] + 1,
                            "score": state["score"] + float(plan.get("score", 0.0)),
                        }
                    )
            next_states.sort(key=lambda state: (state["valid_count"], state["score"]), reverse=True)
            states = next_states[: self.REPAIR_BEAM_WIDTH]

        best: _JointState = states[0] if states else {
            "plans": {},
            "drive_uids": set(),
            "tape_uids": set(),
            "valid_count": 0,
            "score": 0.0,
        }
        result: AllocationResult = {}
        for role in failed_roles:
            plan = best["plans"].get(role)
            if plan is None:
                result[role] = {"valid": False, "reason": reasons[role]}
            else:
                plan.pop("stat_priority_key", None)
                result[role] = plan
        return result
