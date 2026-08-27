# 共享的目标 HP/正式身份相容图与一对一匹配算法。
"""Pure compatibility graph helpers shared by inference and replay mapping."""

from __future__ import annotations

import re
from collections.abc import Sequence


HP_ABSOLUTE_TOLERANCE = 1.0
_MONSTER_KEY = re.compile(r"(?i)(boss|mon)_0*(\d+)")


def normalized_monster_key(value: object) -> str:
    """Normalize only formal boss/monster identifiers; names are not evidence."""

    match = _MONSTER_KEY.search(str(value or ""))
    return "" if match is None else f"{match.group(1).lower()}_{int(match.group(2))}"


def hp_compatible(left: float, right: float) -> bool:
    """Absorb at most one HP point of protocol representation error."""

    return abs(float(left) - float(right)) <= HP_ABSOLUTE_TOLERANCE


def target_compatible(
    captured_monster_id: str,
    initial_max_hp: float,
    *,
    expected_max_hp: float,
    expected_monster_ids: Sequence[str],
) -> bool:
    if not hp_compatible(initial_max_hp, expected_max_hp):
        return False
    captured_key = normalized_monster_key(captured_monster_id)
    if not captured_key:
        return True
    return captured_key in {
        normalized_monster_key(value) for value in expected_monster_ids
    }


def has_injective_matching(
    edges: Sequence[tuple[int, ...]],
    *,
    pinned_observed: int | None = None,
    pinned_slot: int | None = None,
) -> bool:
    """Return whether every observation can consume one distinct static slot."""

    assigned: dict[int, int] = {}
    if pinned_observed is not None and pinned_slot is not None:
        if pinned_slot not in edges[pinned_observed]:
            return False
        assigned[pinned_slot] = pinned_observed

    def assign(observed_index: int, seen: set[int]) -> bool:
        if observed_index == pinned_observed:
            return True
        for slot_index in edges[observed_index]:
            if slot_index == pinned_slot or slot_index in seen:
                continue
            seen.add(slot_index)
            owner = assigned.get(slot_index)
            if owner is None or assign(owner, seen):
                assigned[slot_index] = observed_index
                return True
        return False

    return all(assign(index, set()) for index in range(len(edges)))


def feasible_slots(
    edges: Sequence[tuple[int, ...]],
) -> tuple[tuple[int, ...], ...]:
    """Keep only edges that participate in at least one complete injection."""

    if not edges or not has_injective_matching(edges):
        return ()
    return tuple(
        tuple(
            slot_index
            for slot_index in candidates
            if has_injective_matching(
                edges,
                pinned_observed=observed_index,
                pinned_slot=slot_index,
            )
        )
        for observed_index, candidates in enumerate(edges)
    )


class BattleTargetCandidateGraphService:
    """Public Qt-free graph contract for environment and instance mapping."""

    hp_compatible = staticmethod(hp_compatible)
    target_compatible = staticmethod(target_compatible)
    has_injective_matching = staticmethod(has_injective_matching)
    feasible_slots = staticmethod(feasible_slots)
