# 按所选环境的冻结目标档案，把捕获 target_id 映射到逐击敌方条件。
"""Resolve captured target instances without using the primary target as fallback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.domain.battle_report import BattleAnalysisSnapshot, BattleTargetCondition
from src.domain.battle_target import (
    BattleSelectedTargetProfile,
    BattleTargetInstanceResolution,
)
from src.services.battle_encounter_candidate_selection_service import (
    BattleEncounterCandidateSelectionService,
)
from src.services.battle_target_candidate_graph_service import (
    feasible_slots,
    target_compatible,
)
from src.services.battle_target_observation_support import target_observation_aliases


def _stable_monster_id_key(value: str) -> tuple[str, str]:
    """Deterministic fallback while frozen v35 profiles have no catalog rank."""

    return value.casefold(), value


def _stable_profile_key(profile: BattleSelectedTargetProfile) -> tuple[str, ...]:
    return (
        *_stable_monster_id_key(profile.static_target_id),
        profile.selection_target_id.casefold(),
        profile.selection_target_id,
        profile.monster_class_path.casefold(),
        profile.monster_class_path,
        profile.target_name.casefold(),
        profile.target_name,
        profile.profile_set.casefold(),
        profile.profile_set,
        profile.pack_id.casefold(),
        profile.pack_id,
    )


def _profile_matches_half(
    scope_half: str,
    profile: BattleSelectedTargetProfile,
) -> bool:
    selection_id = profile.selection_target_id
    profile_half = (
        "upper" if "FirstHalf" in selection_id
        else "lower" if "SecondHalf" in selection_id
        else ""
    )
    return not scope_half or not profile_half or scope_half == profile_half


def _feasible_slots_by_half(
    observed: Sequence[tuple[str, str, str, float, int]],
    edges: Sequence[tuple[int, ...]],
) -> tuple[tuple[int, ...], ...]:
    """Keep one malformed half from invalidating the other half's mapping."""

    grouped: dict[str, list[int]] = {}
    for index, row in enumerate(observed):
        grouped.setdefault(row[0], []).append(index)
    result: list[tuple[int, ...]] = [()] * len(observed)
    for indexes in grouped.values():
        feasible = feasible_slots(tuple(edges[index] for index in indexes))
        if not feasible:
            continue
        for local_index, observed_index in enumerate(indexes):
            result[observed_index] = feasible[local_index]
    return tuple(result)


def _condition_signature(condition: BattleTargetCondition) -> tuple[object, ...]:
    return (
        round(condition.enemy_level, 6),
        (
            None
            if condition.enemy_defense_base is None
            else round(condition.enemy_defense_base, 6)
        ),
        round(condition.enemy_defense_up, 9),
        round(condition.enemy_defense_add, 6),
        round(condition.enemy_topple_limit, 6),
        tuple((key, round(value, 9)) for key, value in condition.resistances),
    )


def _condition_for_profile(
    base: BattleTargetCondition,
    profile: BattleSelectedTargetProfile,
) -> BattleTargetCondition:
    return replace(
        base,
        target_name=profile.target_name,
        enemy_level=profile.monster_level,
        resistances=profile.resistances,
        enemy_defense_base=profile.defense_base,
        enemy_defense_up=profile.defense_up,
        enemy_defense_add=profile.defense_add,
        enemy_topple_limit=profile.topple_limit,
    )


class BattleTargetInstanceMappingService:
    """Build target-scoped replay conditions from saved encounter evidence."""

    @classmethod
    def resolve(
        cls,
        evidence: Mapping[str, object] | None,
        condition: BattleTargetCondition | None,
    ) -> tuple[BattleTargetInstanceResolution, ...]:
        if condition is None or not condition.selected_target_profiles:
            return ()
        observed = tuple(
            (
                row.scope_half,
                row.target_id,
                row.monster_id,
                row.initial_max_hp,
                row.first_time_us,
            )
            for row in BattleEncounterCandidateSelectionService.observe(
                evidence,
                combat_context_kind=(
                    "abyss"
                    if condition.environment_kind == "outer_realm"
                    else "non_abyss"
                ),
            )
        )
        expected_half = (
            "upper"
            if "FirstHalf" in condition.environment_ref
            else "lower" if "SecondHalf" in condition.environment_ref else ""
        )
        if expected_half:
            observed = tuple(row for row in observed if row[0] == expected_half)
        profiles = condition.selected_target_profiles
        roster_mapping = (
            condition.environment_kind in {"outer_realm", "feast"}
            or condition.environment_ref.startswith("clone|")
        )
        slots = tuple(
            profile
            for profile in profiles
            for _ in range(profile.monster_count if roster_mapping else 1)
        )
        edges = tuple(
            tuple(
                index
                for index, profile in enumerate(slots)
                if _profile_matches_half(row[0], profile)
                and cls._compatible(row[2], row[3], profile)
            )
            for row in observed
        )
        graph_feasible = (
            _feasible_slots_by_half(observed, edges)
            if roster_mapping
            else edges
        )
        if condition.source_kind == "user_confirmed":
            graph_feasible = tuple(
                feasible or edges[index]
                for index, feasible in enumerate(graph_feasible)
            )
        result = []
        for observed_index, row in enumerate(observed):
            candidate_indexes = graph_feasible[observed_index]
            candidate_profiles = tuple(dict.fromkeys(
                slots[index] for index in candidate_indexes
            ))
            result.append(cls._resolution(row, condition, candidate_profiles))
        by_key = {
            (row.scope_half, row.captured_target_id): row
            for row in result
        }
        hits = evidence.get("hits") if isinstance(evidence, Mapping) else ()
        outgoing = tuple(
            row
            for row in hits or ()
            if isinstance(row, Mapping)
            and str(row.get("direction") or "").casefold() == "outgoing"
        )
        for (half, alias_id), canonical_id in sorted(
            target_observation_aliases(outgoing).items()
        ):
            canonical = by_key.get((half, canonical_id)) or by_key.get(("", canonical_id))
            if canonical is not None:
                result.append(replace(canonical, captured_target_id=alias_id))
        return tuple(result)

    @staticmethod
    def condition_for_hit(
        analysis: BattleAnalysisSnapshot,
        hit,
    ) -> BattleTargetCondition | None:
        key = (
            str(getattr(hit, "scope_half", "") or "").casefold(),
            str(getattr(hit, "target_id", "") or ""),
        )
        resolutions = {
            (row.scope_half.casefold(), row.captured_target_id): row
            for row in analysis.target_instance_resolutions
        }
        resolution = resolutions.get(key)
        if resolution is None and not key[0]:
            resolution = resolutions.get(("", key[1]))
        return None if resolution is None else resolution.target_condition

    @classmethod
    def analysis_for_hit(
        cls,
        analysis: BattleAnalysisSnapshot,
        hit,
    ) -> BattleAnalysisSnapshot:
        if getattr(analysis, "target_instance_mapping_required", False):
            return replace(
                analysis,
                target_condition=cls.condition_for_hit(analysis, hit),
            )
        if getattr(analysis, "target_condition", None) is not None:
            return analysis
        condition = dict(
            getattr(analysis, "target_conditions_by_half", ())
        ).get(
            getattr(hit, "scope_half", "")
        )
        return analysis if condition is None else replace(
            analysis, target_condition=condition
        )

    @staticmethod
    def _compatible(
        captured_monster_id: str,
        initial_max_hp: float,
        profile: BattleSelectedTargetProfile,
    ) -> bool:
        return target_compatible(
            captured_monster_id,
            initial_max_hp,
            expected_max_hp=profile.max_hp,
            expected_monster_ids=(profile.static_target_id, profile.monster_class_path),
        )

    @staticmethod
    def _resolution(
        observed: tuple[str, str, str, float, int],
        base: BattleTargetCondition,
        profiles: Sequence[BattleSelectedTargetProfile],
    ) -> BattleTargetInstanceResolution:
        half, target_id, captured_monster_id, max_hp, _ = observed
        stable_profiles = tuple(sorted(profiles, key=_stable_profile_key))
        monster_ids = tuple(sorted(
            {
                profile.static_target_id
                for profile in stable_profiles
                if profile.static_target_id
            },
            key=_stable_monster_id_key,
        ))
        target_conditions = tuple(
            _condition_for_profile(base, profile) for profile in stable_profiles
        )
        signatures = {
            _condition_signature(condition) for condition in target_conditions
        }
        resolved_monster_id = monster_ids[0] if len(monster_ids) == 1 else ""
        default_monster_id = ""
        if not stable_profiles:
            mode = "unknown"
            target_condition = None
        elif len(signatures) != 1:
            mode = "ambiguous"
            target_condition = None
        else:
            # Replay consumes this already-frozen common condition. The derived
            # default ID below never becomes a static-data lookup key.
            target_condition = replace(
                target_conditions[0], resolved_monster_id=resolved_monster_id
            )
            if captured_monster_id and resolved_monster_id:
                mode = "core_exact"
            elif resolved_monster_id:
                mode = "environment_hp_unique"
            else:
                mode = "profile_equivalent"
            default_monster_id = (
                resolved_monster_id
                if resolved_monster_id
                else (monster_ids[0] if monster_ids else "")
            )
        return BattleTargetInstanceResolution(
            scope_half=half,
            captured_target_id=target_id,
            resolved_monster_id=resolved_monster_id,
            default_monster_id=default_monster_id,
            possible_monster_ids=monster_ids,
            resolution_mode=mode,
            initial_max_hp=max_hp,
            target_condition=target_condition,
        )
