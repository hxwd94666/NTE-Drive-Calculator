# 管理角色优先分配中同分驱动的延迟归属和稳定回填。
"""Keep interchangeable early-drive choices available to later priority groups.

The state is deliberately independent from a UI preview: it records only the
slot-to-UID reservation relation and exposes a small transactional API to the
allocator.  A candidate may be consumed only when every earlier reservation
still has a one-to-one completion.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.models.equipment import Drive


@dataclass(frozen=True)
class DeferredDriveSlot:
    """One finalized blueprint slot whose UID can be chosen after later groups."""

    key: str
    group_index: int
    role_name: str
    slot_type: str
    slot_index: int
    baseline_uid: str
    candidate_uids: tuple[str, ...]
    shape_id: str = ""


@dataclass(frozen=True)
class DeferredRegistration:
    """Pool update returned after a priority group has been analyzed."""

    fixed_uids: frozenset[str]
    exposed_drives: tuple[Drive, ...]
    deferred_slot_count: int


class DeferredDriveReservationState:
    """Preserve a feasible matching for all delayed blueprint slots."""

    def __init__(self) -> None:
        self._slots: list[DeferredDriveSlot] = []
        self._drives_by_uid: dict[str, Drive] = {}
        self._consumed_uids: set[str] = set()

    @property
    def has_slots(self) -> bool:
        return bool(self._slots)

    @property
    def reservation_uids(self) -> frozenset[str]:
        return frozenset(
            uid for slot in self._slots for uid in slot.candidate_uids
        )

    @property
    def consumed_uids(self) -> frozenset[str]:
        return frozenset(self._consumed_uids)

    @property
    def active_slot_count(self) -> int:
        """Return the number of delayed slots that still need a completion."""

        return len(self._slots)

    @property
    def remaining_candidate_uids(self) -> tuple[tuple[str, ...], ...]:
        """Freeze the remaining candidate relation in deterministic slot order."""

        return tuple(
            tuple(uid for uid in slot.candidate_uids if uid not in self._consumed_uids)
            for slot in sorted(self._slots, key=lambda slot: (slot.group_index, slot.key))
        )

    @property
    def remaining_slots(self) -> tuple[DeferredDriveSlot, ...]:
        """Expose the active relation without leaking mutable reservation state."""

        return tuple(
            DeferredDriveSlot(
                key=slot.key,
                group_index=slot.group_index,
                role_name=slot.role_name,
                slot_type=slot.slot_type,
                slot_index=slot.slot_index,
                baseline_uid=slot.baseline_uid,
                candidate_uids=tuple(
                    uid for uid in slot.candidate_uids
                    if uid not in self._consumed_uids
                ),
                shape_id=slot.shape_id,
            )
            for slot in sorted(self._slots, key=lambda slot: (slot.group_index, slot.key))
        )

    def protection_key(self, uid: str) -> tuple[float, str]:
        """Order a conflicting UID by its frozen owner-side base score only."""

        owner_scores = [
            float(self._drives_by_uid[uid].role_scores.get(slot.role_name, 0.0))
            for slot in self._slots
            if uid in slot.candidate_uids and uid in self._drives_by_uid
        ]
        return (min(owner_scores, default=0.0), uid)

    def shape_for_uid(self, uid: str) -> str | None:
        drive = self._drives_by_uid.get(uid)
        return str(drive.shape_id) if drive is not None else None

    def drive_for_uid(self, uid: str) -> Drive | None:
        """Expose a frozen candidate drive for bounded recovery work."""

        return self._drives_by_uid.get(uid)

    def matching_size_after_consuming(self, uids: Iterable[str]) -> int:
        """Measure the best remaining reservation matching after a tentative use.

        Unlike :meth:`can_consume`, this deliberately returns a partial matching
        size.  It is only a diagnostic for choosing the next progressive
        protection target; committing still requires every slot to be filled.
        """

        proposed = set(uids)
        if proposed & self._consumed_uids:
            return -1
        available = {
            uid
            for slot in self._slots
            for uid in slot.candidate_uids
            if uid not in self._consumed_uids and uid not in proposed
        }
        return len(self._maximum_matching(available))

    def effective_blocker_uids(self, used_uids: Iterable[str]) -> tuple[str, ...]:
        """Return used reservation UIDs whose release improves match cardinality.

        The test is intentionally local to the failed current plan: a UID is an
        effective blocker exactly when making that one UID available raises the
        maximum cardinality of the prior-slot bipartite matching.  Recomputing
        after every protection round naturally handles multiple blockers without
        exploring exponential protection subsets.
        """

        used = set(used_uids) - self._consumed_uids
        candidates = sorted(used & self.reservation_uids)
        baseline = self.matching_size_after_consuming(used)
        return tuple(
            uid for uid in candidates
            if self.matching_size_after_consuming(used - {uid}) > baseline
        )

    def slot(self, key: str) -> DeferredDriveSlot:
        for slot in self._slots:
            if slot.key == key:
                return slot
        raise KeyError(key)

    def register(
        self,
        descriptors: Iterable[tuple[DeferredDriveSlot, Iterable[Drive]]],
    ) -> DeferredRegistration:
        """Register one completed priority group's interchangeable slots.

        A selected UID becomes fixed when an initially interchangeable slot
        loses all but one candidate after fixed peers are removed.  Repeating
        that reduction avoids exposing a UID needed by a newly fixed peer.
        """

        pending: list[DeferredDriveSlot] = []
        selected_uids: set[str] = set()
        descriptor_drives: dict[str, Drive] = {}
        for slot, drives in descriptors:
            selected_uids.add(slot.baseline_uid)
            for drive in drives:
                descriptor_drives[drive.uid] = drive
            if slot.baseline_uid not in self._consumed_uids and len(slot.candidate_uids) > 1:
                pending.append(slot)

        fixed_uids = {
            uid for uid in selected_uids
            if uid not in {slot.baseline_uid for slot in pending}
        }
        while True:
            reduced: list[DeferredDriveSlot] = []
            newly_fixed: set[str] = set()
            for slot in pending:
                candidates = tuple(
                    uid for uid in slot.candidate_uids
                    if uid not in fixed_uids and uid not in self._consumed_uids
                )
                if len(candidates) < 2:
                    newly_fixed.add(slot.baseline_uid)
                    continue
                reduced.append(
                    DeferredDriveSlot(
                        key=slot.key,
                        group_index=slot.group_index,
                        role_name=slot.role_name,
                        slot_type=slot.slot_type,
                        slot_index=slot.slot_index,
                        baseline_uid=slot.baseline_uid,
                        candidate_uids=candidates,
                        shape_id=slot.shape_id,
                    )
                )
            if not newly_fixed - fixed_uids:
                pending = reduced
                break
            fixed_uids.update(newly_fixed)
            pending = reduced

        self._slots.extend(pending)
        for uid, drive in descriptor_drives.items():
            self._drives_by_uid.setdefault(uid, drive)
        exposed_uids = {
            uid
            for slot in pending
            for uid in slot.candidate_uids
            if uid not in self._consumed_uids
        }
        return DeferredRegistration(
            fixed_uids=frozenset(fixed_uids),
            exposed_drives=tuple(
                self._drives_by_uid[uid]
                for uid in sorted(exposed_uids)
                if uid in self._drives_by_uid
            ),
            deferred_slot_count=len(pending),
        )

    def can_consume(self, uids: Iterable[str]) -> bool:
        """Check whether consuming UIDs leaves every prior slot matchable."""

        proposed = set(uids)
        if proposed & self._consumed_uids:
            return False
        available = {
            uid
            for slot in self._slots
            for uid in slot.candidate_uids
            if uid not in self._consumed_uids and uid not in proposed
        }
        return len(self._maximum_matching(available)) == len(self._slots)

    def commit(self, uids: Iterable[str]) -> None:
        """Commit a later group's drive use after its whole plan is valid."""

        values = set(uids)
        if not self.can_consume(values):
            raise ValueError("预留驱动回填匹配被耗尽")
        self._consumed_uids.update(values)

    def finalize(self) -> dict[str, Drive]:
        """Return a deterministic slot-to-drive completion for final plans."""

        available = {
            uid
            for slot in self._slots
            for uid in slot.candidate_uids
            if uid not in self._consumed_uids
        }
        matched = self._maximum_matching(available)
        if len(matched) != len(self._slots):
            raise ValueError("预留驱动缺少可回填的一对一匹配")
        return {
            slot_key: self._drives_by_uid[uid]
            for slot_key, uid in matched.items()
        }

    def _maximum_matching(self, available: set[str]) -> dict[str, str]:
        """Use deterministic DFS and return the largest attainable matching."""

        assigned_uid_to_slot: dict[str, str] = {}
        slots = sorted(self._slots, key=lambda slot: (slot.group_index, slot.key))

        def assign(slot: DeferredDriveSlot, visited: set[str]) -> bool:
            for uid in slot.candidate_uids:
                if uid not in available or uid in visited:
                    continue
                visited.add(uid)
                incumbent_key = assigned_uid_to_slot.get(uid)
                if incumbent_key is None or assign(by_key[incumbent_key], visited):
                    assigned_uid_to_slot[uid] = slot.key
                    return True
            return False

        by_key = {slot.key: slot for slot in slots}
        for slot in slots:
            assign(slot, set())
        return {slot_key: uid for uid, slot_key in assigned_uid_to_slot.items()}


class DeferredDriveReservationMixin:
    """Build exact-equivalence reservations from completed role-priority plans."""

    def _register_deferred_group_slots(
        self,
        state: DeferredDriveReservationState,
        group_index: int,
        allocation: dict,
        full_drives: Iterable[Drive],
        stat_priority_configs: dict[str, dict],
        crit_rate_caps: dict[str, float],
        unavailable_uids: set[str],
    ) -> DeferredRegistration:
        all_drives = {
            drive.uid: drive for drive in full_drives
            if drive.uid not in unavailable_uids
        }
        descriptors: list[tuple[DeferredDriveSlot, list[Drive]]] = []
        for role_name, plan in allocation.items():
            if not plan.get("valid"):
                continue
            for slot_type, field in (
                ("set", "assigned_set_drives"),
                ("extra", "assigned_extra_drives"),
            ):
                assigned = list(plan.get(field) or ())
                for slot_index, baseline in enumerate(assigned):
                    candidates = self._deferred_equivalent_drives(
                        role_name,
                        plan,
                        slot_type,
                        slot_index,
                        baseline,
                        all_drives.values(),
                        stat_priority_configs.get(role_name),
                        crit_rate_caps,
                    )
                    slot = DeferredDriveSlot(
                        key=f"{group_index}:{role_name}:{slot_type}:{slot_index}",
                        group_index=group_index,
                        role_name=role_name,
                        slot_type=slot_type,
                        slot_index=slot_index,
                        baseline_uid=baseline.uid,
                        candidate_uids=tuple(drive.uid for drive in candidates),
                        shape_id=baseline.shape_id,
                    )
                    descriptors.append((slot, candidates))
        return state.register(descriptors)

    def _deferred_equivalent_drives(
        self,
        role_name: str,
        plan: dict,
        slot_type: str,
        slot_index: int,
        baseline: Drive,
        source: Iterable[Drive],
        config: dict | None,
        crit_rate_caps: dict[str, float],
    ) -> list[Drive]:
        """Return drives that leave this already-selected role plan unchanged."""

        set_drives = list(plan.get("assigned_set_drives") or ())
        extra_drives = list(plan.get("assigned_extra_drives") or ())
        selected = set_drives if slot_type == "set" else extra_drives
        baseline_items = [*set_drives, *extra_drives]
        baseline_key = self._stat_priority_key_for_items(role_name, baseline_items, config)
        replacement_index = slot_index
        prior_items = (
            set_drives[:slot_index]
            if slot_type == "set"
            else [*set_drives, *extra_drives[:slot_index]]
        )
        current_crit = self._current_role_crit(
            role_name, plan.get("assigned_tape"), prior_items,
        )
        include_extra_shape_bonus = self._slot_uses_extra_shape_bonus(
            slot_type, plan.get("blueprint"),
        )
        baseline_pick_key = self._drive_pick_key(
            role_name,
            baseline,
            baseline.role_scores.get(role_name, 0.0),
            config,
            include_extra_shape_bonus=include_extra_shape_bonus,
            current_crit=current_crit,
        )
        candidates: list[Drive] = []
        for candidate in source:
            if candidate.uid != baseline.uid and any(
                drive.uid == candidate.uid for drive in baseline_items
            ):
                continue
            if candidate.shape_id != baseline.shape_id:
                continue
            if not self._item_allowed_for_role(candidate, config):
                continue
            if candidate.role_scores.get(role_name, 0.0) != baseline.role_scores.get(role_name, 0.0):
                continue
            if self._drive_area(candidate) != self._drive_area(baseline):
                continue
            if self._item_crit_rate(candidate) != self._item_crit_rate(baseline):
                continue
            if self._stat_priority_depth(role_name, candidate, config) != self._stat_priority_depth(
                role_name, baseline, config,
            ):
                continue
            if self._drive_pick_key(
                role_name,
                candidate,
                candidate.role_scores.get(role_name, 0.0),
                config,
                include_extra_shape_bonus=include_extra_shape_bonus,
                current_crit=current_crit,
            ) != baseline_pick_key:
                continue
            replaced_set = list(set_drives)
            replaced_extra = list(extra_drives)
            (replaced_set if slot_type == "set" else replaced_extra)[replacement_index] = candidate
            replaced_items = [*replaced_set, *replaced_extra]
            if self._stat_priority_key_for_items(role_name, replaced_items, config) != baseline_key:
                continue
            items_with_tape = [plan.get("assigned_tape"), *replaced_items]
            if not self._within_crit_rate_cap(role_name, items_with_tape, crit_rate_caps):
                continue
            if self._crit_floor_failure_reason(
                role_name, plan.get("assigned_tape"), replaced_items, config,
            ):
                continue
            candidates.append(candidate)
        return sorted(candidates, key=lambda drive: drive.uid)
