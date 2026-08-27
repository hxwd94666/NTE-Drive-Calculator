# 以完整遭遇的逐目标初始生命证据缩小环境候选并稳定选择默认。
"""Infer a default encounter without changing immutable Core hit facts."""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.domain.battle_encounter import (
    BattleEncounterCandidate,
    BattleEncounterCandidateMatch,
    BattleEncounterTargetPreset,
    BattleObservedTarget,
)
from src.domain.battle_report import BattleAnalysisSnapshot, BattleTargetCondition
from src.domain.battle_target import BattleTargetInstanceResolution
from src.services.battle_encounter_candidate_selection_service import (
    BattleEncounterCandidateSelectionService,
)
from src.services.battle_encounter_catalog_service import BattleEncounterCatalogService
from src.services.battle_inferred_target_resolution_support import (
    inferred_mapping_condition,
    project_inferred_target_evidence,
)
from src.services.battle_mixed_outer_realm_inference_service import (
    infer_mixed_outer_realm,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


INFERRED_ENCOUNTER_SOURCE_KIND = "inferred_encounter_hp_injective_default"
INFERRED_ENCOUNTER_ALGORITHM_VERSION = "battle-encounter-hp-residual-v2"


@dataclass(frozen=True, slots=True)
class BattleInferredTargetIdentity:
    scope_half: str
    captured_target_id: str
    target_name: str
    inferred_monster_id: str
    initial_max_hp: float


@dataclass(frozen=True, slots=True)
class BattleInferredEncounter:
    environment_kind: str
    environment_ref: str
    environment_name: str
    source_kind: str
    confidence: str
    inference_basis: str
    scope_half: str
    outer_realm_floor: int | None
    difficulty_id: int | None
    feast_options: tuple[tuple[str, str], ...]
    targets: tuple[BattleEncounterTargetPreset, ...]
    identities: tuple[BattleInferredTargetIdentity, ...]
    target_condition: BattleTargetCondition | None
    ambiguous: bool = False
    ambiguity_alternatives: tuple[str, ...] = ()
    target_conditions_by_half: tuple[tuple[str, BattleTargetCondition], ...] = ()
    target_mapping_conditions_by_half: tuple[tuple[str, BattleTargetCondition], ...] = ()
    selection_mode: str = "unique_hard"
    default_reason: str = ""
    alternative_environment_refs: tuple[str, ...] = ()
    formula_matches: tuple[BattleEncounterCandidateMatch, ...] = ()
    formula_profile_conflict: bool = False
    residual_fit_score: float | None = None
    residual_fit_gap: float | None = None
    algorithm_version: str = INFERRED_ENCOUNTER_ALGORITHM_VERSION


def _shared_profile(
    targets: Sequence[BattleEncounterTargetPreset],
) -> tuple[object, ...] | None:
    signatures = {
        (
            target.monster_level,
            target.defense_base,
            target.defense_up,
            target.defense_add,
            target.topple_limit,
            target.resistances,
        )
        for target in targets
    }
    if len(signatures) != 1:
        return None
    signature = next(iter(signatures))
    return None if signature[1] is None else signature


def _formula_profile_fingerprint(
    candidate: BattleEncounterCandidate,
) -> tuple[tuple[object, ...], ...]:
    profiles = []
    for target in candidate.targets:
        profile = (
            target.monster_level,
            target.defense_base,
            target.defense_up,
            target.defense_add,
            target.topple_limit,
            target.resistances,
        )
        profiles.extend(profile for _ in range(max(1, target.monster_count)))
    return tuple(sorted(profiles, key=repr))


def _identity_candidates_ambiguous(
    matches: Sequence[BattleEncounterCandidateMatch],
) -> bool:
    observed_count = max(
        (len(match.possible_target_indexes) for match in matches),
        default=0,
    )
    for observed_index in range(observed_count):
        identities = set()
        for match in matches:
            if observed_index >= len(match.possible_target_indexes):
                continue
            for target_index in match.possible_target_indexes[observed_index]:
                target = match.candidate.targets[target_index]
                identities.add(
                    ("id", target.target_id)
                    if target.target_id
                    else ("class", target.monster_class_path)
                    if target.monster_class_path
                    else ("name", target.target_name)
                )
        if len(identities) > 1:
            return True
    return False


class BattleInferredTargetConditionService:
    """Match lower-bound target instances and select one reproducible default."""

    @classmethod
    def infer(
        cls,
        *,
        static_database_path: Path | None,
        combat_context_kind: str,
        floor: int | None,
        evidence: Mapping[str, Any] | None,
        range_start_us: int | None,
        range_end_us: int | None,
    ) -> BattleInferredEncounter | None:
        del range_start_us, range_end_us  # UI detail range never scopes recognition.
        if static_database_path is None:
            return None
        mixed = infer_mixed_outer_realm(
            static_database_path=static_database_path,
            combat_context_kind=combat_context_kind,
            floor=floor,
            evidence=evidence,
            range_start_us=None,
            range_end_us=None,
            infer_half=cls.infer,
        )
        if mixed is not None:
            return mixed
        observed = BattleEncounterCandidateSelectionService.observe(
            evidence,
            combat_context_kind=combat_context_kind,
        )
        if not observed:
            return None
        observed_halves = {row.scope_half for row in observed if row.scope_half}
        if len(observed_halves) > 1:
            return None
        scope_half = next(iter(observed_halves), "")
        try:
            with StaticGameDataDao(static_database_path) as static_dao:
                candidates = BattleEncounterCatalogService.filter_hard_context(
                    BattleEncounterCatalogService.load(static_dao),
                    combat_context_kind=combat_context_kind,
                    floor=floor,
                    scope_half=scope_half,
                )
                selection = BattleEncounterCandidateSelectionService.select_default(
                    BattleEncounterCandidateSelectionService.strict_matches(
                        observed,
                        candidates,
                    )
                )
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return None
        if selection is None:
            return None

        match = selection.default
        candidate = match.candidate
        matched_rows = (selection.default, *selection.alternatives)
        matched_candidates = tuple(row.candidate for row in matched_rows)
        profile_ambiguous = len({
            _formula_profile_fingerprint(row) for row in matched_candidates
        }) > 1
        identity_ambiguous = _identity_candidates_ambiguous(matched_rows)
        identities = (
            ()
            if profile_ambiguous or identity_ambiguous
            else cls._assign_identities(observed, match)
        )
        # A non-empty strict pool must always produce a usable derived profile.
        # Residual fitting may replace this deterministic default later, but the
        # analysis must never silently fall back to "no enemy condition".
        condition = cls.condition_for_candidate(candidate)
        mapping_condition = inferred_mapping_condition(
            candidate,
            source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
        )
        hp_text = ", ".join(
            f"{row.initial_max_hp:,.0f}" for row in observed
        )
        alternatives = tuple(
            row.candidate.environment_name for row in selection.alternatives
        )
        return BattleInferredEncounter(
            environment_kind=candidate.environment_kind,
            environment_ref=candidate.environment_ref,
            environment_name=candidate.environment_name,
            source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
            confidence=selection.confidence,
            inference_basis=(
                f"完整遭遇/半场的 {len(observed)} 个目标实例及初始最大生命"
                f" [{hp_text}] 均可一对一注入默认环境的静态槽位；"
                f"{selection.default_reason}"
                + (
                    "；严格候选的公式画像不等价，先使用稳定默认画像参与公式，"
                    "并保留全部冲突候选进入原始逐击残差裁决。"
                    if profile_ambiguous
                    else "；严格候选公式画像等价，但身份未唯一，仅保留稳定默认身份。"
                    if identity_ambiguous
                    else ""
                )
            ),
            scope_half=candidate.scope_half,
            outer_realm_floor=candidate.outer_realm_floor,
            difficulty_id=candidate.difficulty_id,
            feast_options=candidate.feast_options,
            targets=candidate.targets,
            identities=identities,
            target_condition=condition,
            ambiguous=bool(alternatives),
            ambiguity_alternatives=alternatives,
            target_conditions_by_half=(
                () if condition is None else ((candidate.scope_half, condition),)
            ),
            target_mapping_conditions_by_half=(
                ()
                if mapping_condition is None
                else ((candidate.scope_half, mapping_condition),)
            ),
            selection_mode=selection.selection_mode,
            default_reason=selection.default_reason,
            alternative_environment_refs=tuple(
                row.candidate.environment_ref for row in selection.alternatives
            ),
            formula_matches=(selection.default, *selection.alternatives),
            formula_profile_conflict=profile_ambiguous,
        )

    @classmethod
    def select_residual_candidate(
        cls,
        inferred: BattleInferredEncounter,
        *,
        environment_ref: str,
        confidence: str,
        selection_mode: str,
        score: float,
        score_gap: float,
        audit_basis: str,
    ) -> BattleInferredEncounter:
        """Promote one strict candidate selected by raw replay residuals."""

        selected = next(
            (
                row
                for row in inferred.formula_matches
                if row.candidate.environment_ref == environment_ref
            ),
            None,
        )
        if selected is None:
            return inferred
        candidate = selected.candidate
        condition = cls.condition_for_candidate(candidate)
        mapping_condition = inferred_mapping_condition(
            candidate,
            source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
        )
        alternatives = tuple(
            row.candidate
            for row in inferred.formula_matches
            if row.candidate.environment_ref != environment_ref
        )
        return replace(
            inferred,
            environment_kind=candidate.environment_kind,
            environment_ref=candidate.environment_ref,
            environment_name=candidate.environment_name,
            confidence=confidence,
            inference_basis=(
                f"{inferred.inference_basis}；{audit_basis}"
            ),
            scope_half=candidate.scope_half,
            outer_realm_floor=candidate.outer_realm_floor,
            difficulty_id=candidate.difficulty_id,
            feast_options=candidate.feast_options,
            targets=candidate.targets,
            identities=(),
            target_condition=condition,
            ambiguous=bool(alternatives),
            ambiguity_alternatives=tuple(
                row.environment_name for row in alternatives
            ),
            target_conditions_by_half=(
                () if condition is None else ((candidate.scope_half, condition),)
            ),
            target_mapping_conditions_by_half=(
                ()
                if mapping_condition is None
                else ((candidate.scope_half, mapping_condition),)
            ),
            selection_mode=selection_mode,
            default_reason=audit_basis,
            alternative_environment_refs=tuple(
                row.environment_ref for row in alternatives
            ),
            residual_fit_score=float(score),
            residual_fit_gap=float(score_gap),
        )

    @staticmethod
    def apply_residual_resolution_metadata(
        inferred: BattleInferredEncounter,
        resolutions: tuple[BattleTargetInstanceResolution, ...],
    ) -> tuple[BattleTargetInstanceResolution, ...]:
        """Keep all conflicting identities while consuming the fitted profile."""

        if not inferred.formula_profile_conflict:
            return resolutions
        result = []
        for resolution in resolutions:
            possible_ids = tuple(sorted({
                target.target_id
                for match in inferred.formula_matches
                for target in match.candidate.targets
                if target.target_id
                and abs(target.max_hp - resolution.initial_max_hp) <= 1.0
            }, key=lambda value: (value.casefold(), value)))
            selected_ids = tuple(sorted({
                target.target_id
                for target in inferred.targets
                if target.target_id
                and abs(target.max_hp - resolution.initial_max_hp) <= 1.0
            }, key=lambda value: (value.casefold(), value)))
            selected_id = selected_ids[0] if len(selected_ids) == 1 else ""
            fitted = (
                inferred.selection_mode == "robust_fit"
                and inferred.residual_fit_gap is not None
                and inferred.residual_fit_gap > 0.0
                and inferred.confidence == "高"
            )
            result.append(replace(
                resolution,
                resolved_monster_id=(selected_id if fitted else ""),
                default_monster_id=selected_id,
                possible_monster_ids=possible_ids,
                resolution_mode=("robust_fit" if fitted else "ambiguous"),
                target_condition=(
                    None
                    if resolution.target_condition is None
                    else replace(
                        resolution.target_condition,
                        resolved_monster_id=(selected_id if fitted else ""),
                    )
                ),
            ))
        return tuple(result)

    @staticmethod
    def project_evidence(
        evidence: dict[str, Any] | None,
        inferred: BattleInferredEncounter | None,
    ) -> None:
        """Project derived names in memory without changing persisted axis rows."""

        project_inferred_target_evidence(evidence, inferred)

    @staticmethod
    def apply(
        analysis: BattleAnalysisSnapshot,
        inferred: BattleInferredEncounter | None,
    ) -> BattleAnalysisSnapshot:
        if inferred is None:
            return analysis
        return replace(
            analysis,
            target_condition=(
                analysis.target_condition
                if analysis.target_condition is not None
                else inferred.target_condition
            ),
            detected_environment_kind=inferred.environment_kind,
            detected_environment_ref=inferred.environment_ref,
            detected_environment_name=inferred.environment_name,
            detected_environment_difficulty_id=inferred.difficulty_id,
            detected_environment_options=inferred.feast_options,
            detected_outer_realm_floor=inferred.outer_realm_floor,
            target_identity_inference_source=inferred.source_kind,
            target_identity_inference_confidence=inferred.confidence,
            target_identity_inference_basis=inferred.inference_basis,
            target_identity_inference_ambiguous=inferred.ambiguous,
            target_identity_inference_alternatives=inferred.ambiguity_alternatives,
            target_conditions_by_half=inferred.target_conditions_by_half,
        )

    @staticmethod
    def _assign_identities(
        observed: Sequence[BattleObservedTarget],
        match: BattleEncounterCandidateMatch,
    ) -> tuple[BattleInferredTargetIdentity, ...]:
        resolved = []
        for row, target_indexes in zip(
            observed,
            match.possible_target_indexes,
            strict=True,
        ):
            targets = tuple(match.candidate.targets[index] for index in target_indexes)
            names = {target.target_name for target in targets if target.target_name}
            if len(names) != 1:
                continue
            name = next(iter(names))
            monster_ids = {target.target_id for target in targets if target.target_id}
            monster_id = next(iter(monster_ids)) if len(monster_ids) == 1 else ""
            resolved.append((row, name, monster_id))

        name_counts = Counter(name for _, name, _ in resolved)
        name_ordinals: Counter[str] = Counter()
        identities = []
        for row, name, monster_id in resolved:
            name_ordinals[name] += 1
            display_name = (
                f"{name} {name_ordinals[name]}" if name_counts[name] > 1 else name
            )
            identities.append(BattleInferredTargetIdentity(
                scope_half=row.scope_half,
                captured_target_id=row.target_id,
                target_name=display_name,
                inferred_monster_id=monster_id,
                initial_max_hp=row.initial_max_hp,
            ))
        return tuple(identities)

    @staticmethod
    def condition_for_candidate(
        candidate: BattleEncounterCandidate,
    ) -> BattleTargetCondition | None:
        profile = _shared_profile(candidate.targets)
        if profile is None:
            return inferred_mapping_condition(
                candidate,
                source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
            )
        level, defense_base, defense_up, defense_add, topple_limit, resistances = profile
        static_ids = tuple(dict.fromkeys(
            target.target_id for target in candidate.targets if target.target_id
        ))
        condition_kind = (
            "open_world" if candidate.environment_kind == "clone"
            else candidate.environment_kind
        )
        return BattleTargetCondition(
            target_name=candidate.environment_name,
            enemy_level=float(level or 1.0),
            scene=(
                "open_world"
                if candidate.environment_kind in {"open_world", "clone"}
                else "outer_realm"
            ),
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=resistances,
            source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
            enemy_defense_base=float(defense_base),
            enemy_defense_up=float(defense_up),
            enemy_defense_add=float(defense_add),
            enemy_topple_limit=float(topple_limit),
            environment_kind=condition_kind,
            environment_ref=candidate.environment_ref,
            selected_target_ids=static_ids,
            primary_target_id=static_ids[0] if len(static_ids) == 1 else "",
            difficulty_id=candidate.difficulty_id,
            feast_options=candidate.feast_options,
        )
