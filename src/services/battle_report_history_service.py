# 通过窄服务边界读取和管理账号战报历史。
"""Read and manage account battle history through a narrow service boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from src.domain.battle_report import BattleAnalysisSnapshot, BattleRetentionMutation
from src.domain.battle_build_assumption import (
    GRADUATION_ASSUMPTION_TITLE, has_graduation_assumption,
)
from src.services.battle_counterfactual_analysis_service import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_build_profile_normalization_service import (
    normalize_inferred_battle_build,
)
from src.services.battle_report_history_projection import (
    analysis_scope_range,
    character_analysis_scopes,
    retention_mutation,
)
from src.services.battle_report_history_support import (
    BattleReportHistoryDaoMixin,
    StaleBattleReportContextError,
)
from src.services.battle_build_equipment_service import (
    battle_equipment_items,
)
from src.services.battle_build_editor_inventory_pool_service import (
    current_inventory_replacement_items,
)
from src.services.battle_build_edit_projection_service import apply_battle_build_edit
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidate,
    BattleMarginalCandidateService,
)
from src.services.battle_inferred_character_fact_service import (
    BattleInferredCharacterFactService,
)
from src.services.battle_import_equipment_projection_service import (
    apply_import_equipment_locks,
)
from src.services.battle_build_edit_history_mixin import (
    BattleBuildEditHistoryMixin,
)
from src.services.battle_report_replay_history_mixin import (
    BattleReportReplayHistoryMixin,
)
from src.services.character_shape_bonus_service import (
    static_character_shape_profile_fields,
)
from src.services.battle_animation_window_service import (
    BattleAnimationWindowService,
)
from src.services.battle_action_inference_service import (
    BattleActionAnimationCandidate,
)
from src.services.official_role_page_service import load_official_role_detail
from src.services.skill_name_rendering_service import (
    SkillNameRenderingService,
)
from src.services.battle_report_history_evidence_projection import (
    BattleReportHistoryEvidenceProjectionMixin,
)
from src.services.battle_report_persistence_service import (
    BattleReportContextGuard,
    BattleReportPersistenceDependencies,
)
from src.services.battle_hit_replay_service import (
    HIT_REPLAY_MODEL_VERSION,
    BattleHitReplayService,
)
from src.services.battle_fork_critical_inference_service import (
    BattleForkCriticalInferenceService,
)
from src.services.battle_formal_damage_tag_service import (
    BattleFormalDamageTagService,
)
from src.services.battle_marginal_counterfactual_projection_service import (
    BattleMarginalCounterfactualProjectionService,
)
from src.services.battle_inferred_target_condition_service import (
    BattleInferredTargetConditionService,
)
from src.services.battle_inferred_target_snapshot_service import (
    BattleInferredTargetSnapshotService,
)
from src.services.battle_inferred_target_resolution_support import (
    project_resolved_target_evidence,
    resolve_available_target_instances,
)
from src.services.battle_outer_realm_buff_service import BattleOuterRealmBuffService
from src.services.battle_target_condition_history_mixin import (
    BattleTargetConditionHistoryMixin,
)
from src.services.battle_environment_condition_service import (
    resolve_battle_target_condition,
)
from src.services.battle_build_stat_reconstruction_service import (
    BattleBuildStatReconstructionService,
)
from src.services.battle_analysis_progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_hit_projection_preparation_service import (
    BattleHitProjectionPreparationService,
)
from src.storage.sqlite.user_data_dao import UserDataError
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


__all__ = ["BattleReportHistoryService", "StaleBattleReportContextError"]


class BattleReportHistoryService(
    BattleReportHistoryEvidenceProjectionMixin,
    BattleBuildEditHistoryMixin,
    BattleReportReplayHistoryMixin,
    BattleTargetConditionHistoryMixin,
    BattleReportHistoryDaoMixin,
):
    def __init__(
        self,
        *,
        dependencies: BattleReportPersistenceDependencies,
        context_is_current: BattleReportContextGuard,
    ) -> None:
        self._dependencies = BattleReportPersistenceDependencies(
            account_id=str(dependencies.account_id),
            user_database_path=Path(dependencies.user_database_path).resolve(),
            generation=int(dependencies.generation),
            static_database_path=(
                None
                if dependencies.static_database_path is None
                else Path(dependencies.static_database_path).resolve()
            ),
        )
        self._context_is_current = context_is_current
        self._skill_name_renderer: SkillNameRenderingService | None = None
        self._animation_candidate_cache: dict[
            tuple[tuple[int, ...], tuple[str, ...]],
            tuple[BattleActionAnimationCandidate, ...],
        ] = {}
        self._target_catalog_cache: dict[str, Any] | None = None
        self._inferred_encounter_cache: dict[int, Any] = {}
        self._inferred_encounter_fit_cache: dict[int, Any] = {}

    def load_analysis(
        self,
        battle_record_id: int,
        *,
        start_us: int | None = None,
        end_us: int | None = None,
        detail_scope: str | None = None,
        use_build_edit: bool = True,
        marginal_candidate: BattleMarginalCandidate | None = None,
        disabled_inferred_fact_ids: frozenset[str] = frozenset(),
        include_buff_inference: bool = True,
        include_hit_replays: bool = True,
        include_buff_counterfactuals: bool = True,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> BattleAnalysisSnapshot | None:
        """Load and project all long-page sections from one evidence snapshot."""

        report_battle_analysis_progress(
            progress_callback,
            phase="load",
            message="正在读取冻结战报、逐击轴和角色配置…",
        )
        with self._open_current_dao() as user_dao:
            record = user_dao.load_battle_record(battle_record_id)
            if record is None:
                return None
            evidence = user_dao.load_battle_axis_evidence(battle_record_id)
            build = user_dao.load_battle_build_snapshot(battle_record_id)
            build_edit = user_dao.load_battle_build_edit(battle_record_id)
            import_equipment_locks = user_dao.load_battle_import_equipment_locks(
                battle_record_id
            )
            saved_target_condition = user_dao.load_battle_target_condition(
                battle_record_id
            )
            inferred_snapshot = user_dao.load_battle_inferred_target_snapshot(
                battle_record_id
            )
        saved_target_condition = self._complete_target_condition(
            saved_target_condition, evidence
        )
        target_condition = resolve_battle_target_condition(saved_target_condition)
        build = normalize_inferred_battle_build(build)
        apply_import_equipment_locks(build, import_equipment_locks)
        recognition_build = None
        self._localize_axis_evidence(evidence)
        report_battle_analysis_progress(
            progress_callback,
            phase="prepare",
            message="正在整理逐击身份、目标实例和冻结角色输入…",
        )
        BattleFormalDamageTagService.project(
            evidence,
            self._dependencies.static_database_path,
        )
        if start_us is None and end_us is None:
            scoped_range = analysis_scope_range(
                evidence, record["raw_summary_payload"], detail_scope
            )
            if scoped_range is not None:
                start_us, end_us = scoped_range
        record_floor = record.get("abyss_floor")
        inferred_encounter = None
        encounter_fit_cached = False
        persisted_inferred_encounter = None
        if target_condition is None:
            (
                inferred_encounter,
                encounter_fit_cached,
                persisted_inferred_encounter,
            ) = BattleInferredTargetSnapshotService.resolve(
                persisted_row=inferred_snapshot,
                battle_record_id=battle_record_id,
                fitted_cache=self._inferred_encounter_fit_cache,
                inferred_cache=self._inferred_encounter_cache,
                static_database_path=self._dependencies.static_database_path,
                combat_context_kind=str(record.get("combat_context_kind") or ""),
                floor=None if record_floor is None else int(record_floor),
                evidence=evidence,
                battle_occurred_at_utc=record.get("captured_at_utc"),
                dependencies=self._dependencies,
                context_is_current=self._context_is_current,
            )
        needs_encounter_fit = bool(
            target_condition is None
            and inferred_encounter is not None
            and inferred_encounter.formula_profile_conflict
            and not encounter_fit_cached
        )
        BattleInferredTargetConditionService.project_evidence(
            evidence,
            inferred_encounter,
        )
        analysis_target_condition = (
            target_condition
            or (None if inferred_encounter is None else inferred_encounter.target_condition)
        )
        target_instance_resolutions, target_instance_mapping_required = (
            resolve_available_target_instances(
                evidence,
                target_condition,
                inferred_encounter,
                static_database_path=self._dependencies.static_database_path,
            )
        )
        project_resolved_target_evidence(evidence, target_instance_resolutions)
        target_control_policy = self._load_target_control_policy(
            target_instance_resolutions
        )
        inferred_character_facts = BattleInferredCharacterFactService.infer(evidence)
        if needs_encounter_fit and build is not None:
            recognition_build = deepcopy(build)
            BattleInferredCharacterFactService.apply_to_build(
                recognition_build,
                inferred_character_facts,
                disabled_fact_ids=frozenset(),
            )
            BattleBuildStatReconstructionService.enrich(
                recognition_build,
                self._dependencies,
            )
        effective_disabled_fact_ids = frozenset(disabled_inferred_fact_ids)
        if marginal_candidate is not None:
            effective_disabled_fact_ids |= marginal_candidate.disabled_inferred_fact_ids
        if build is not None:
            if marginal_candidate is not None:
                apply_battle_build_edit(
                    build,
                    BattleMarginalCandidateService.as_build_edit(
                        marginal_candidate, frozen_build=build,
                    ),
                )
            elif use_build_edit:
                apply_battle_build_edit(build, build_edit)
            BattleInferredCharacterFactService.apply_to_build(
                build,
                inferred_character_facts,
                disabled_fact_ids=effective_disabled_fact_ids,
            )
            BattleBuildStatReconstructionService.enrich(build, self._dependencies)
        if include_buff_counterfactuals:
            include_hit_replays = True
        if include_hit_replays:
            include_buff_inference = True
        if needs_encounter_fit:
            include_buff_inference = True
        animation_candidates = self._load_animation_candidates(evidence, build)
        buff_rules = self._load_buff_rules(build) if include_buff_inference else ()
        zankou_form_config = (
            self._load_zankou_form_config(build)
            if include_buff_inference
            else None
        )
        outer_realm_buff_config = BattleOuterRealmBuffService.load(
            self._dependencies.static_database_path,
            (
                analysis_target_condition.environment_ref
                if analysis_target_condition is not None
                else ("" if inferred_encounter is None else inferred_encounter.environment_ref)
            ),
        ) if include_buff_inference else None
        _analyze = partial(BattleCounterfactualAnalysisService.analyze,
            battle_record_id=battle_record_id,
            evidence=evidence,
            build=build,
            capability_level=str(
                record.get("evidence_capability_level")
                or ("hit_axis" if evidence is not None else None)
                or record.get("capability_level")
                or "summary_only"
            ),
            requested_start_us=start_us,
            requested_end_us=end_us,
            animation_candidates=animation_candidates,
            character_elements=self._load_character_elements(build),
            buff_rules=buff_rules,
            target_condition=analysis_target_condition,
            zankou_form_config=zankou_form_config,
            shinku_rage_config=(
                self._load_shinku_rage_config(build) if include_buff_inference else None
            ),
            outer_realm_buff_config=outer_realm_buff_config,
            infer_buffs=include_buff_inference,
            target_control_policy=target_control_policy,
        )
        if needs_encounter_fit and inferred_encounter is not None:
            fit_analyze = partial(
                BattleCounterfactualAnalysisService.analyze,
                battle_record_id=battle_record_id,
                evidence=evidence,
                build=recognition_build,
                capability_level=str(
                    record.get("evidence_capability_level")
                    or ("hit_axis" if evidence is not None else None)
                    or record.get("capability_level")
                    or "summary_only"
                ),
                requested_start_us=None,
                requested_end_us=None,
                animation_candidates=self._load_animation_candidates(
                    evidence,
                    recognition_build,
                ),
                character_elements=self._load_character_elements(recognition_build),
                buff_rules=self._load_buff_rules(recognition_build),
                target_condition=analysis_target_condition,
                zankou_form_config=self._load_zankou_form_config(
                    recognition_build
                ),
                outer_realm_buff_config=outer_realm_buff_config,
                shinku_rage_config=self._load_shinku_rage_config(recognition_build),
                infer_buffs=True,
            )
            inferred_encounter = self._fit_inferred_encounter(
                inferred_encounter,
                analyze=fit_analyze,
                build=recognition_build,
                evidence=evidence,
                inferred_character_facts=inferred_character_facts,
            )
            self._inferred_encounter_cache[battle_record_id] = inferred_encounter
            self._inferred_encounter_fit_cache[battle_record_id] = inferred_encounter
            analysis_target_condition = inferred_encounter.target_condition
            outer_realm_buff_config = BattleOuterRealmBuffService.load(
                self._dependencies.static_database_path,
                inferred_encounter.environment_ref,
            )
            _analyze = partial(
                _analyze,
                target_condition=analysis_target_condition,
                outer_realm_buff_config=outer_realm_buff_config,
            )
            target_instance_resolutions, target_instance_mapping_required = (
                resolve_available_target_instances(
                    evidence,
                    None,
                    inferred_encounter,
                    static_database_path=self._dependencies.static_database_path,
                )
            )
            target_instance_resolutions = (
                BattleInferredTargetConditionService.apply_residual_resolution_metadata(
                    inferred_encounter,
                    target_instance_resolutions,
                )
            )
            project_resolved_target_evidence(evidence, target_instance_resolutions)
            target_control_policy = self._load_target_control_policy(
                target_instance_resolutions
            )
            _analyze = partial(
                _analyze,
                target_control_policy=target_control_policy,
            )
        if (
            target_condition is None
            and inferred_encounter is not None
            and (
                persisted_inferred_encounter is None
                or inferred_encounter != persisted_inferred_encounter
            )
        ):
            BattleInferredTargetSnapshotService.persist(
                dependencies=self._dependencies,
                context_is_current=self._context_is_current,
                battle_record_id=battle_record_id,
                inferred=inferred_encounter,
            )
        report_battle_analysis_progress(
            progress_callback,
            phase="analyze",
            message="正在推断动作、治疗、Buff 区间和伤害构成…",
        )
        analysis = replace(
            _analyze(),
            inferred_character_facts=inferred_character_facts,
        )
        analysis = BattleInferredTargetConditionService.apply(
            analysis,
            inferred_encounter,
        )
        analysis = replace(
            analysis,
            target_instance_resolutions=target_instance_resolutions,
            target_instance_mapping_required=target_instance_mapping_required,
        )
        if not include_hit_replays:
            return analysis
        report_battle_analysis_progress(
            progress_callback,
            phase="replay",
            message="正在执行原始固定轴逐击公式重放…",
        )
        skill_evidence = self._load_skill_damage_evidence(analysis, build)
        topple_character_configs = self._load_topple_character_configs(analysis)
        prepared_projections = BattleHitProjectionPreparationService.prepare(
            analysis,
            skill_evidence,
        )
        analysis = replace(
            analysis,
            hit_replays=BattleHitReplayService.replay(
                analysis,
                skill_evidence,
                topple_character_configs=topple_character_configs,
                buff_interval_index=prepared_projections.interval_index,
                projection_by_event=prepared_projections.formula_by_event,
                progress_callback=progress_callback,
                progress_phase="replay",
                progress_message="正在执行原始固定轴逐击公式重放…",
            ),
            hit_replay_model_version=HIT_REPLAY_MODEL_VERSION,
            detected_environment_kind=(
                analysis.detected_environment_kind
                or (
                    "outer_realm"
                    if str(record.get("combat_context_kind")) == "abyss"
                    else ""
                )
            ),
            detected_outer_realm_floor=(
                analysis.detected_outer_realm_floor
                if analysis.detected_outer_realm_floor is not None
                else (
                    None
                    if record.get("abyss_floor") is None
                    else int(record["abyss_floor"])
                )
            ),
        )
        critical_events = BattleForkCriticalInferenceService.infer(
            analysis.hits,
            analysis.hit_replays,
            buff_rules,
        )
        if critical_events:
            report_battle_analysis_progress(
                progress_callback,
                phase="critical_replay",
                message="检测到弧盘暴击触发，正在重建冻结分支…",
            )
            replayed = analysis
            analysis = BattleInferredTargetConditionService.apply(
                replace(
                    _analyze(critical_events=critical_events),
                    inferred_character_facts=inferred_character_facts,
                ),
                inferred_encounter,
            )
            analysis = replace(
                analysis,
                target_instance_resolutions=target_instance_resolutions,
                target_instance_mapping_required=target_instance_mapping_required,
            )
            prepared_projections = BattleHitProjectionPreparationService.prepare(
                analysis,
                skill_evidence,
            )
            analysis = replace(
                analysis,
                hit_replays=BattleHitReplayService.replay(
                    analysis,
                    skill_evidence,
                    topple_character_configs=topple_character_configs,
                    buff_interval_index=prepared_projections.interval_index,
                    projection_by_event=prepared_projections.formula_by_event,
                    progress_callback=progress_callback,
                    progress_phase="critical_replay",
                    progress_message="正在重放弧盘暴击触发后的固定轴…",
                ),
                hit_replay_model_version=HIT_REPLAY_MODEL_VERSION,
                detected_environment_kind=replayed.detected_environment_kind,
                detected_outer_realm_floor=replayed.detected_outer_realm_floor,
            )
        if not include_buff_counterfactuals:
            return analysis
        report_battle_analysis_progress(
            progress_callback,
            phase="counterfactual",
            message="正在规划 Buff 与被动反事实工作量…",
        )
        return BattleMarginalCounterfactualProjectionService.apply(
            analysis,
            build,
            skill_evidence,
            topple_character_configs=topple_character_configs,
            progress_callback=progress_callback,
            interval_index=prepared_projections.interval_index,
            original_projection_by_event=(
                prepared_projections.beneficiary_by_event
            ),
        )

    def _load_animation_candidates(
        self,
        evidence: dict[str, Any] | None,
        build: dict[str, Any] | None,
    ) -> tuple[BattleActionAnimationCandidate, ...]:
        static_path = self._dependencies.static_database_path
        if static_path is None:
            return ()
        hits = tuple((evidence or {}).get("hits") or ())
        character_ids = tuple(
            sorted(
                {
                    int(value)
                    for value in (
                        *(row.get("character_id") for row in hits),
                        *(
                            row.get("character_id")
                            for row in ((build or {}).get("characters") or ())
                        ),
                    )
                    if value is not None
                }
            )
        )
        ability_ids = tuple(
            sorted(
                {
                    str(row.get("ability_name") or "").strip()
                    for row in hits
                    if str(row.get("ability_name") or "").strip()
                },
                key=str.casefold,
            )
        )
        if not character_ids or not ability_ids:
            return ()
        cache_key = (character_ids, tuple(value.casefold() for value in ability_ids))
        cached = self._animation_candidate_cache.get(cache_key)
        if cached is not None:
            return cached
        try:
            with StaticGameDataDao(static_path) as static_dao:
                candidates = BattleAnimationWindowService.load_candidates(
                    static_dao,
                    character_ids=character_ids,
                    ability_ids=ability_ids,
                )
        except (OSError, RuntimeError, ValueError):
            candidates = ()
        self._animation_candidate_cache[cache_key] = candidates
        return candidates

    def save_record(self, battle_record_id: int) -> BattleRetentionMutation:
        with self._open_current_dao() as user_dao:
            result = user_dao.promote_battle_record_to_manual(battle_record_id)
        return retention_mutation(result)

    def unmark_record(self, battle_record_id: int) -> BattleRetentionMutation:
        with self._open_current_dao() as user_dao:
            result = user_dao.unmark_manual_battle_record(battle_record_id)
        return retention_mutation(result)

    def delete_record(self, battle_record_id: int) -> bool:
        self._inferred_encounter_cache.pop(battle_record_id, None)
        self._inferred_encounter_fit_cache.pop(battle_record_id, None)
        with self._open_current_dao() as user_dao:
            return user_dao.delete_battle_record(battle_record_id)

    def update_page_state(
        self,
        *,
        battle_record_id: int | None,
        detail_scope: str,
    ) -> dict[str, Any]:
        with self._open_current_dao() as user_dao:
            return user_dao.update_battle_report_page_state(
                battle_record_id=battle_record_id,
                detail_scope=detail_scope,
            )

    def update_analysis_state(
        self,
        *,
        battle_record_id: int,
        start_us: int | None,
        end_us: int | None,
        character_id: int | None = None,
    ) -> dict[str, Any]:
        with self._open_current_dao() as user_dao:
            return user_dao.update_battle_report_analysis_state(
                battle_record_id=battle_record_id,
                start_us=start_us,
                end_us=end_us,
                character_id=character_id,
            )

    def load_build_editor_data(
        self,
        battle_record_id: int,
        *,
        seed_from_role_page: bool = False,
    ) -> dict[str, Any]:
        """Build role-page editor models without mutating the immutable snapshot."""

        self._assert_counterfactual_editable(battle_record_id)
        with self._open_current_dao() as user_dao:
            build = user_dao.load_battle_build_snapshot(battle_record_id)
            build_edit = user_dao.load_battle_build_edit(battle_record_id)
            evidence = user_dao.load_battle_axis_evidence(battle_record_id)
            import_origin = user_dao.load_battle_report_import_origin(
                battle_record_id
            )
            import_equipment_locks = user_dao.load_battle_import_equipment_locks(
                battle_record_id
            )
            assumed_equipment = has_graduation_assumption(build)
            equipment_editable = import_origin is None and not assumed_equipment
            battle_replacement_items = current_inventory_replacement_items(
                user_dao,
                equipment_editable=equipment_editable,
            )
        if build is None:
            raise UserDataError("当前战报没有可编辑的角色配置快照")
        apply_import_equipment_locks(build, import_equipment_locks)
        static_path = self._dependencies.static_database_path
        if static_path is None:
            raise UserDataError("当前应用没有可用的官方静态数据库")
        with StaticGameDataDao(static_path) as static_dao:
            shape_fields_by_character = {
                int(original["character_id"]): static_character_shape_profile_fields(
                    static_dao, int(original["character_id"])
                )
                for original in build.get("characters") or ()
            }
        edited_by_character = {
            int(row["character_id"]): row
            for row in ((build_edit or {}).get("characters") or ())
        }
        scopes_by_character = character_analysis_scopes(evidence)
        details: list[dict[str, Any]] = []
        detail_request_cache: dict[object, Any] = {}
        for original in build.get("characters") or ():
            character_id = int(original["character_id"])
            detail = load_official_role_detail(
                self._dependencies.user_database_path,
                character_id,
                include_inventory_contexts=True,
                static_database_path=static_path,
                request_cache=detail_request_cache,
            )
            edited = edited_by_character.get(character_id)
            edited_profile = dict((edited or {}).get("profile") or {})
            if seed_from_role_page:
                profile = dict(detail.get("profile") or {})
                seed_source = "current_role_page"
                if edited_profile:
                    for key in (
                        "equipment_context_key",
                        "equipment_context_title",
                        "equipment_source_kind",
                        "equipment_override",
                    ):
                        if key in edited_profile:
                            profile[key] = edited_profile[key]
            elif edited is not None:
                profile = dict(edited_profile)
                seed_source = "edited_copy"
            else:
                profile = dict(original.get("profile") or {})
                seed_source = "battle_frozen"
            profile.update({
                "character_id": character_id,
                "observed_name": original.get("observed_name"),
                "ordinal": int(original.get("ordinal") or 0),
            })
            profile.update(shape_fields_by_character[character_id])
            frozen_items = battle_equipment_items(original)
            role_contexts = (
                dict(detail.get("equipment_contexts") or {})
                if equipment_editable
                else {}
            )
            for key, context in role_contexts.items():
                if key == "current":
                    context["source_kind"] = "role_page_current"
                elif key == "saved" or key.startswith("saved:"):
                    context["source_kind"] = "role_page_saved"
                context["source_title"] = str(context.get("title") or key)
            source_title = (
                GRADUATION_ASSUMPTION_TITLE if assumed_equipment else
                ("本场原始冻结配装" if equipment_editable else "导入包固化配装")
            )
            equipment_contexts = {
                "battle": {
                    "title": source_title,
                    "source_title": source_title,
                    "source_kind": (
                        "graduation_assumed" if assumed_equipment else
                        ("battle_frozen" if equipment_editable else "imported_locked")
                    ),
                    "items": frozen_items,
                    "calculation_items": frozen_items,
                    "replacement_items": battle_replacement_items,
                    "available": bool(frozen_items),
                }
            }
            if "equipment_override" in profile:
                edited_items = battle_equipment_items(
                    {"equipment": profile.get("equipment_override") or ()}
                )
                source_title = str(
                    profile.get("equipment_context_title") or "修改副本配装"
                )
                equipment_contexts["edited"] = {
                    "title": f"修改副本 · {source_title}",
                    "source_title": source_title,
                    "source_kind": str(
                        profile.get("equipment_source_kind") or "edited_copy"
                    ),
                    "items": edited_items,
                    "calculation_items": edited_items,
                    "replacement_items": battle_replacement_items,
                    "available": bool(edited_items),
                }
            equipment_contexts.update(role_contexts)
            detail["profile"] = profile
            detail["original_profile"] = dict(original.get("profile") or {})
            detail["editor_seed_source"] = seed_source
            detail["analysis_detail_scope"] = scopes_by_character.get(character_id)
            detail["equipment_contexts"] = equipment_contexts
            detail["equipment_editable"] = equipment_editable
            detail["selected_equipment_context_key"] = (
                "edited" if "equipment_override" in profile else "battle"
            )
            details.append(detail)
        return {
            "battle_record_id": int(battle_record_id),
            "has_edit": build_edit is not None,
            "is_active": bool((build_edit or {}).get("is_active")),
            "inferred_character_facts": (
                BattleInferredCharacterFactService.infer(evidence)
            ),
            "equipment_editable": equipment_editable,
            "report_origin": "local_capture" if import_origin is None else "imported_v2",
            "equipment_assumed": assumed_equipment,
            "details": details,
        }
