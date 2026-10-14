# 通过窄服务边界读取和管理账号战报历史。
"""Read and manage account battle history through a narrow service boundary."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from src.domain.battle_report.analysis.backend_contracts import DirectFormulaBackend, available_battle_compute
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.history.records import BattleRetentionMutation
from src.domain.battle_report.build.inputs import has_graduation_assumption
from src.services.battle_report.build.offline_analysis import (
    BattleCounterfactualAnalysisService,
)
from src.services.battle_report.history.inputs import BattleReportAnalysisInputs
from src.services.battle_report.history.projection import (
    analysis_scope_range,
    retention_mutation,
)
from src.services.battle_report.history.access import (
    BattleReportHistoryDaoMixin,
    StaleBattleReportContextError,
)
from src.services.battle_report.build.inventory_pool import (
    current_inventory_replacement_items,
)
from src.services.battle_report.build.edit_projection import apply_battle_build_edit
from src.domain.battle_report.counterfactual.results import BattleMarginalCandidate
from src.services.battle_report.marginal.candidates import BattleMarginalCandidateService
from src.services.battle_report.characters.inferred_facts import (
    BattleInferredCharacterFactService,
)
from src.services.battle_report.build.equipment import (
    apply_import_equipment_locks,
)
from src.services.battle_report.build.edit_history import (
    BattleBuildEditHistoryMixin,
)
from src.services.battle_report.history.replay import (
    BattleReportReplayHistoryMixin,
)
from src.services.battle_report.actions.animation_window import (
    BattleAnimationWindowService,
)
from src.services.battle_report.actions.inference import (
    BattleActionAnimationCandidate,
)
from src.services.battle_report.history.role_editor import build_role_editor_details
from src.services.official_role.skill_names import (
    SkillNameRenderingService,
)
from src.services.battle_report.history.evidence import (
    BattleReportHistoryEvidenceProjectionMixin,
)
from src.services.battle_report.persistence import (
    BattleReportContextGuard,
    BattleReportPersistenceDependencies,
)
from src.services.battle_report.replay.service import (
    HIT_REPLAY_MODEL_VERSION,
    BattleHitReplayService,
)
from src.services.battle_report.forks.critical_inference import (
    BattleForkCriticalInferenceService,
)
from src.services.battle_report.marginal.counterfactual_projection import (
    BattleMarginalCounterfactualProjectionService,
)
from src.services.battle_report.targets.inferred_condition import (
    BattleInferredTargetConditionService,
)
from src.services.battle_report.targets.inferred_snapshot import (
    BattleInferredTargetSnapshotService,
)
from src.services.battle_report.targets.resolution import (
    project_resolved_target_evidence,
    resolve_available_target_instances,
)
from src.services.battle_report.environment.outer_realm_buffs import BattleOuterRealmBuffService
from src.services.battle_report.targets.history import (
    BattleTargetConditionHistoryMixin,
)
from src.services.battle_report.environment.conditions import (
    resolve_battle_target_condition,
)
from src.services.battle_report.build.stat_reconstruction import (
    BattleBuildStatReconstructionService,
)
from src.services.battle_report.analysis.progress import (
    BattleAnalysisProgressCallback,
    report_battle_analysis_progress,
)
from src.services.battle_report.replay.preparation import BattleHitProjectionPreparationService
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
        direct_formula_backend: DirectFormulaBackend | None = None,
        native_page_loader=None,
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
        self._direct_formula_backend = direct_formula_backend
        self.native_page_loader = native_page_loader
        self._skill_name_renderer: SkillNameRenderingService | None = None
        self._animation_candidate_cache: dict[
            tuple[tuple[int, ...], tuple[str, ...]],
            tuple[BattleActionAnimationCandidate, ...],
        ] = {}
        self._target_catalog_cache: dict[str, Any] | None = None
        self._inferred_encounter_cache: dict[int, Any] = {}
        self._inferred_encounter_fit_cache: dict[int, Any] = {}

    def freeze_analysis_inputs(self, battle_record_id: int) -> BattleReportAnalysisInputs:
        """离线差分一次冻结事实；正式页面仍由原生只读接口读取。"""
        return BattleReportAnalysisInputs.load(self, battle_record_id)

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
        projection_memo: BattleBuffProjectionMemo | None = None,
        frozen_inputs: BattleReportAnalysisInputs | None = None,
    ) -> BattleAnalysisSnapshot | None:
        """DEPRECATED：旧 Python 分析，仅供离线差分；正式页面使用原生 load。"""
        projection_memo = projection_memo if projection_memo is not None else BattleBuffProjectionMemo()
        projection_memo.bind_backend(self._direct_formula_backend, progress_callback)
        report_battle_analysis_progress(
            progress_callback,
            phase="load",
            message="正在读取冻结战报、逐击轴和角色配置…",
        )
        inputs = frozen_inputs if frozen_inputs is not None else self.freeze_analysis_inputs(battle_record_id)
        record, evidence, build, build_edit, saved_target_condition = inputs.copy_for(self, battle_record_id)
        if record is None:
            return None
        # 派生目标拟合允许在本次请求中持久化；后续候选须读取已完成的新拟合。
        with self._open_current_dao() as user_dao:
            inferred_snapshot = user_dao.load_battle_inferred_target_snapshot(battle_record_id)
        target_condition = resolve_battle_target_condition(saved_target_condition)
        recognition_build = None
        report_battle_analysis_progress(
            progress_callback,
            phase="prepare",
            message="正在整理逐击身份、目标实例和冻结角色输入…",
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
                detail_cache=inputs.role_detail_cache_for(self, battle_record_id),
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
            BattleBuildStatReconstructionService.enrich(
                build, self._dependencies, detail_cache=inputs.role_detail_cache_for(self, battle_record_id),
            )
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
            compute_backend=available_battle_compute(self._direct_formula_backend),
            checkpoint=lambda: report_battle_analysis_progress(
                progress_callback, phase="prepare", message="正在批量推断冻结 Buff 状态…",
            ),
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
                compute_backend=available_battle_compute(self._direct_formula_backend),
                checkpoint=lambda: report_battle_analysis_progress(
                    progress_callback, phase="prepare", message="正在准备候选目标的冻结状态…",
                ),
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
                progress_callback=progress_callback,
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
        skill_evidence = self._load_skill_damage_evidence(analysis, build, progress_callback)
        topple_character_configs = self._load_topple_character_configs(analysis)
        prepared_projections = BattleHitProjectionPreparationService.prepare(
            analysis,
            skill_evidence,
            projection_memo=projection_memo,
        )
        analysis = replace(
            analysis,
            hit_replays=BattleHitReplayService.replay(
                analysis,
                skill_evidence,
                topple_character_configs=topple_character_configs,
                direct_formula_backend=self._direct_formula_backend,
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
                projection_memo=projection_memo,
            )
            analysis = replace(
                analysis,
                hit_replays=BattleHitReplayService.replay(
                    analysis,
                    skill_evidence,
                    topple_character_configs=topple_character_configs,
                    direct_formula_backend=self._direct_formula_backend,
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
            direct_formula_backend=self._direct_formula_backend,
            progress_callback=progress_callback,
            interval_index=prepared_projections.interval_index,
            original_projection_by_event=prepared_projections.beneficiary_by_event,
            projection_memo=projection_memo,
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
        """编辑器领取独立投影；保存编辑副本不得改写原始角色/装备快照。"""

        self._assert_counterfactual_editable(battle_record_id)
        editor_facts = self.native_page_loader.load_editor_facts(battle_record_id) if self.native_page_loader is not None else None
        with self._open_current_dao() as user_dao:
            build = user_dao.load_battle_build_snapshot(battle_record_id)
            build_edit = user_dao.load_battle_build_edit(battle_record_id)
            evidence = user_dao.load_battle_axis_evidence(battle_record_id) if editor_facts is None else None
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
        details = build_role_editor_details(
            self._dependencies.user_database_path, static_path, build, build_edit, evidence,
            editor_facts, battle_replacement_items, equipment_editable, assumed_equipment, seed_from_role_page,
        )
        return {
            "battle_record_id": int(battle_record_id),
            "has_edit": build_edit is not None,
            "is_active": bool((build_edit or {}).get("is_active")),
            "inferred_character_facts": editor_facts[0] if editor_facts is not None else BattleInferredCharacterFactService.infer(evidence),
            "equipment_editable": equipment_editable,
            "report_origin": "local_capture" if import_origin is None else "imported_v2",
            "equipment_assumed": assumed_equipment,
            "details": details,
        }
