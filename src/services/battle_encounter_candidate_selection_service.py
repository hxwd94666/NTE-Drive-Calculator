# 从完整遭遇证据建立 HP 相容图并稳定选择默认环境。
"""Qt-free encounter candidate filtering and deterministic default selection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from src.domain.battle_encounter import (
    BattleEncounterCandidate,
    BattleEncounterCandidateMatch,
    BattleEncounterCandidateSelection,
    BattleObservedTarget,
)
from src.services.battle_target_candidate_graph_service import (
    feasible_slots,
    normalized_monster_key,
    target_compatible,
)
from src.services.battle_target_observation_support import target_observation_aliases


def _number(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number <= 0.0 else round(number, 3)


def _candidate_rank(row: BattleEncounterCandidateMatch) -> tuple[object, ...]:
    candidate = row.candidate
    return (
        -row.hard_identity_matches,
        row.unobserved_slot_count,
        candidate.catalog_order,
        candidate.environment_ref.casefold(),
        candidate.environment_ref,
    )


class BattleEncounterCandidateSelectionService:
    """Match lower-bound observations to catalog slots without inventing roster facts."""

    @staticmethod
    def observe(
        evidence: Mapping[str, object] | None,
        *,
        combat_context_kind: str,
    ) -> tuple[BattleObservedTarget, ...]:
        hits = evidence.get("hits") if isinstance(evidence, Mapping) else ()
        outgoing = tuple(
            row
            for row in hits or ()
            if isinstance(row, Mapping)
            and str(row.get("direction") or "").casefold() == "outgoing"
        )
        aliases = target_observation_aliases(outgoing)
        is_outer_realm = str(combat_context_kind or "").casefold() == "abyss"
        states: dict[tuple[str, str], tuple[str, float, int]] = {}
        for row in outgoing:
            raw_half = str(row.get("abyss_half") or "").strip().casefold()
            half = raw_half if is_outer_realm and raw_half in {"upper", "lower"} else ""
            raw_target_id = str(row.get("target_id") or "").strip()
            target_id = aliases.get((raw_half, raw_target_id), raw_target_id)
            max_hp = _number(row.get("target_max_hp"))
            if not target_id or max_hp is None:
                continue
            # Core v4 reports this row's maximum before the structured
            # max-HP-reduction settlement. Adding that delta here would create
            # an impossible value above the encounter's observed opening HP.
            initial_hp = max_hp
            monster_id = str(row.get("target_monster_id") or "").strip()
            time_us = int(row.get("relative_time_us") or 0)
            previous = states.get((half, target_id))
            states[(half, target_id)] = (
                (previous[0] if previous and previous[0] else monster_id),
                max(previous[1], initial_hp) if previous else initial_hp,
                min(previous[2], time_us) if previous else time_us,
            )
        return tuple(sorted(
            (
                BattleObservedTarget(
                    scope_half=half,
                    target_id=target_id,
                    monster_id=values[0],
                    initial_max_hp=values[1],
                    first_time_us=values[2],
                )
                for (half, target_id), values in states.items()
            ),
            key=lambda row: (row.scope_half, row.first_time_us, row.target_id),
        ))

    @staticmethod
    def strict_matches(
        observed: Sequence[BattleObservedTarget],
        candidates: Sequence[BattleEncounterCandidate],
    ) -> tuple[BattleEncounterCandidateMatch, ...]:
        matches = []
        for candidate in candidates:
            slots = tuple(
                (target_index, target)
                for target_index, target in enumerate(candidate.targets)
                for _ in range(max(1, target.monster_count))
            )
            edges = tuple(
                tuple(
                    slot_index
                    for slot_index, (_target_index, target) in enumerate(slots)
                    if target_compatible(
                        row.monster_id,
                        row.initial_max_hp,
                        expected_max_hp=target.max_hp,
                        expected_monster_ids=(target.target_id, target.monster_class_path),
                    )
                )
                for row in observed
            )
            feasible = feasible_slots(edges)
            if not feasible:
                continue
            possible_target_indexes = tuple(
                tuple(sorted({slots[slot_index][0] for slot_index in slot_indexes}))
                for slot_indexes in feasible
            )
            matches.append(BattleEncounterCandidateMatch(
                candidate=candidate,
                possible_target_indexes=possible_target_indexes,
                hard_identity_matches=sum(
                    1 for row in observed if normalized_monster_key(row.monster_id)
                ),
                unobserved_slot_count=len(slots) - len(observed),
            ))
        return tuple(sorted(matches, key=_candidate_rank))

    @staticmethod
    def select_default(
        matches: Sequence[BattleEncounterCandidateMatch],
    ) -> BattleEncounterCandidateSelection | None:
        ranked = tuple(sorted(matches, key=_candidate_rank))
        if not ranked:
            return None
        default = ranked[0]
        alternatives = ranked[1:]
        if not alternatives:
            return BattleEncounterCandidateSelection(
                default=default,
                alternatives=(),
                confidence="高",
                selection_mode="unique_hard",
                default_reason="严格 HP/身份相容池仅有一个环境。",
            )
        confidence = "中" if default.hard_identity_matches else "低"
        return BattleEncounterCandidateSelection(
            default=default,
            alternatives=alternatives,
            confidence=confidence,
            selection_mode="ambiguous_default",
            default_reason=(
                "严格候选不唯一；按正式身份命中数、未观测槽位数、"
                "正式目录顺序和稳定 environment_ref 选择默认环境。"
            ),
        )
