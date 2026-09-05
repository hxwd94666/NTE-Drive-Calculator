# 集中提供历史战报逐击重放输入和严格候选残差投影。
"""Replay-input and encounter-fit helpers for battle report history."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

from src.domain.battle_encounter import BattleEncounterCandidate
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_encounter_fit_projection_service import (
    BattleEncounterFitProjectionService,
)
from src.services.battle_hit_replay_service import (
    HIT_REPLAY_MODEL_VERSION,
    BattleHitReplayService,
)
from src.services.battle_inferred_target_condition_service import (
    BattleInferredEncounter,
    BattleInferredTargetConditionService,
)
from src.services.battle_outer_realm_buff_service import BattleOuterRealmBuffService
from src.services.battle_skill_damage_evidence_service import (
    BattleSkillDamageEvidenceService,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleCharacterConfig,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_target_control_policy_service import (
    BattleTargetControlPolicyService,
    CONTROL_ELIGIBLE_DEFAULT,
)
from src.services.battle_zankou_form_buff_service import (
    BattleZankouFormBuffService,
    BattleZankouFormConfig,
)
from src.services.battle_shinku_rage_buff_service import (
    BattleShinkuRageBuffService,
    BattleShinkuRageConfig,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class BattleReportReplayHistoryMixin:
    _dependencies: Any

    def _load_target_control_policy(self, resolutions: tuple[Any, ...]) -> str:
        static_path = self._dependencies.static_database_path
        resolved_ids = tuple(
            str(row.resolved_monster_id or "")
            for row in resolutions
            if str(row.resolved_monster_id or "")
        )
        if static_path is None or not resolved_ids:
            return CONTROL_ELIGIBLE_DEFAULT
        try:
            with StaticGameDataDao(static_path) as static_dao:
                return BattleTargetControlPolicyService.resolve_formal_policy(
                    static_dao,
                    resolved_ids,
                    all_targets_resolved=(
                        bool(resolutions)
                        and all(
                            str(row.resolved_monster_id or "")
                            for row in resolutions
                        )
                    ),
                )
        except (OSError, RuntimeError, ValueError):
            return CONTROL_ELIGIBLE_DEFAULT

    def _load_skill_damage_evidence(
        self,
        analysis: BattleAnalysisSnapshot,
        build: dict[str, Any] | None,
    ):
        static_path = self._dependencies.static_database_path
        if static_path is None or build is None:
            return ()
        try:
            with StaticGameDataDao(static_path) as static_dao:
                return BattleSkillDamageEvidenceService.load(
                    static_dao,
                    analysis,
                    build,
                )
        except (OSError, RuntimeError, ValueError):
            return ()

    def _load_character_elements(
        self,
        build: dict[str, Any] | None,
    ) -> dict[int, str]:
        """Load static character elements for versioned formula inference only."""
        static_path = self._dependencies.static_database_path
        character_ids = tuple(
            int(row.get("character_id") or 0)
            for row in (build or {}).get("characters") or ()
            if int(row.get("character_id") or 0) > 0
        )
        if static_path is None or not character_ids:
            return {}
        try:
            with StaticGameDataDao(static_path) as static_dao:
                return {
                    character_id: str(
                        (static_dao.get_character(character_id) or {}).get(
                            "element_type"
                        )
                        or ""
                    )
                    for character_id in character_ids
                }
        except (OSError, RuntimeError, ValueError):
            return {}

    def _load_buff_rules(
        self,
        build: dict[str, Any] | None,
    ) -> tuple[BattleStaticBuffRule, ...]:
        static_path = self._dependencies.static_database_path
        if static_path is None or build is None:
            return ()
        try:
            with StaticGameDataDao(static_path) as static_dao:
                return BattleBuffInferenceService.load_rules(static_dao, build)
        except (OSError, RuntimeError, ValueError):
            return ()

    def _load_zankou_form_config(
        self,
        build: dict[str, Any] | None,
    ) -> BattleZankouFormConfig | None:
        static_path = self._dependencies.static_database_path
        characters = tuple((build or {}).get("characters") or ())
        if static_path is None or not any(
            int(row.get("character_id") or 0) == 1036
            for row in characters
        ):
            return None
        try:
            with StaticGameDataDao(static_path) as static_dao:
                return BattleZankouFormBuffService.load_config(static_dao)
        except (OSError, RuntimeError, ValueError):
            return None

    def _load_shinku_rage_config(
        self,
        build: dict[str, Any] | None,
    ) -> BattleShinkuRageConfig | None:
        static_path = self._dependencies.static_database_path
        if static_path is None or not any(
            int(row.get("character_id") or 0) == 1076
            for row in (build or {}).get("characters") or ()
        ):
            return None
        try:
            with StaticGameDataDao(static_path) as static_dao:
                return BattleShinkuRageBuffService.load_config(static_dao)
        except (OSError, RuntimeError, ValueError):
            return None

    def _load_topple_character_configs(
        self,
        analysis: BattleAnalysisSnapshot,
    ) -> dict[int, BattleToppleCharacterConfig]:
        static_path = self._dependencies.static_database_path
        if static_path is None:
            return {}
        configs: dict[int, BattleToppleCharacterConfig] = {}
        try:
            with StaticGameDataDao(static_path) as static_dao:
                for baseline in analysis.baselines:
                    character = static_dao.get_character(baseline.character_id)
                    level_multiplier = static_dao.get_topple_level_multiplier(
                        baseline.character_level
                    )
                    element = str((character or {}).get("element_type") or "")
                    marker = "CHARACTER_ELEMENT_TYPE_"
                    if marker not in element or level_multiplier is None:
                        continue
                    configs[baseline.character_id] = BattleToppleCharacterConfig(
                        character_id=baseline.character_id,
                        damage_attribute=element.rsplit(marker, 1)[-1].casefold(),
                        level_multiplier=level_multiplier,
                    )
        except (OSError, RuntimeError, ValueError):
            return {}
        return configs

    def _fit_inferred_encounter(
        self,
        inferred: BattleInferredEncounter,
        *,
        analyze: Callable[..., BattleAnalysisSnapshot],
        build: dict[str, Any] | None,
        evidence: dict[str, Any] | None,
        inferred_character_facts: tuple[Any, ...],
    ) -> BattleInferredEncounter:
        def project(candidate: BattleEncounterCandidate) -> BattleAnalysisSnapshot:
            condition = BattleInferredTargetConditionService.condition_for_candidate(
                candidate
            )
            outer_config = BattleOuterRealmBuffService.load(
                self._dependencies.static_database_path,
                candidate.environment_ref,
            )
            analysis = replace(
                analyze(
                    target_condition=condition,
                    outer_realm_buff_config=outer_config,
                ),
                inferred_character_facts=inferred_character_facts,
            )
            resolutions = (
                ()
                if condition is None or not condition.selected_target_profiles
                else BattleTargetInstanceMappingService.resolve(evidence, condition)
            )
            if resolutions:
                analysis = replace(
                    analysis,
                    target_instance_resolutions=resolutions,
                    target_instance_mapping_required=True,
                )
            skill_evidence = self._load_skill_damage_evidence(analysis, build)
            topple_configs = self._load_topple_character_configs(analysis)
            return replace(
                analysis,
                hit_replays=BattleHitReplayService.replay(
                    analysis,
                    skill_evidence,
                    topple_character_configs=topple_configs,
                    apply_observed_refinements=False,
                ),
                hit_replay_model_version=HIT_REPLAY_MODEL_VERSION,
            )

        group_analysis = replace(
            analyze(
                target_condition=None,
                outer_realm_buff_config=None,
            ),
            inferred_character_facts=inferred_character_facts,
        )
        outcome = BattleEncounterFitProjectionService.select(
            inferred,
            project_candidate=project,
            group_analysis=group_analysis,
        )
        return inferred if outcome is None else outcome.inferred
