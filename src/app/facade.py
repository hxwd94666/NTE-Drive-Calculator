# 提供解析和分配流程的程序化门面。
"""Programmatic facade for allocation and vision processing."""

from __future__ import annotations

from src.observability.stage_timings import timed, timed_call, timed_context

from pathlib import Path
from copy import deepcopy
from contextlib import nullcontext

from src.app.constants import APP_VERSION
from src.integrations.native_core.nte_core import NteCoreClient
from src.integrations.runtime.bundled_resources import bundled_config_dir
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.results.plan_save import require_allocation_release
from src.services.allocation.execution.baseline_fallback import attach_baseline_fallbacks
from src.integrations.allocation.native_allocation import create_allocation_executor
from src.integrations.allocation.native_blueprints import create_blueprint_executor
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled
from src.integrations.allocation.damage_allocation_job import DeferredDamageBlueprints, execute_damage_job
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.services.allocation.direct.direct_postprocess import direct_strategy_selection_error
from src.services.allocation.direct.direct_group_postprocess import freeze_direct_group_inputs, optimize_standard_direct_groups
from src.services.allocation.axis.axis_team_preflight import prepare_axis_team_selection
from src.services.allocation.axis.axis_postprocess import freeze_axis_allocation_inputs, optimize_frozen_axis
from src.integrations.vision.recognition.batch_processor import BatchProcessor
from src.services.recognition.resources import load_recognition_resources
from src.domain.allocation.failures import explain_allocation_failures
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.storage.sqlite.user_data_dao import UserDataDao
from src.integrations.vision.capture_files import capture_output_lease
from src.services.inventory.scanning.visual_scan_commit import commit_completed_vision_inventory
from src.utils.logger import logger


class NTEAppFacade:
    def __init__(
        self,
        *,
        config_dir: str | Path,
        user_config_dir: str | Path,
        user_database_path: str | Path,
        screenshot_dir: str | Path | None = None,
        log_dir: str | Path | None = None,
        app_dir: str | Path | None = None,
        allocation_static_database_path: str | Path | None = None,
        battle_semantics_path: str | Path | None = None,
    ):
        self.battle_semantics_path = Path(battle_semantics_path) if battle_semantics_path is not None else bundled_config_dir() / "gameplay_effect_semantics.json"
        self.config_dir = str(config_dir)
        self.user_config_dir = str(user_config_dir)
        self.user_database_path = Path(user_database_path)
        self.screenshot_dir = (
            Path(screenshot_dir) if screenshot_dir is not None else None
        )
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.app_dir = Path(app_dir) if app_dir is not None else None
        self.allocation_static_database_path = (
            Path(allocation_static_database_path)
            if allocation_static_database_path is not None else None
        )

    def execute_vision_processing(self, input_dir=None):
        resolved_input_dir = (
            Path(input_dir) if input_dir is not None else self.screenshot_dir
        )
        if resolved_input_dir is None:
            raise ValueError("视觉解析必须显式提供截图目录")
        with capture_output_lease(resolved_input_dir):
            logger.info("开始视觉解析...")
            processor = BatchProcessor(
                input_dir=str(resolved_input_dir),
                config_dir=self.config_dir,
                resources=load_recognition_resources(self.config_dir, self.allocation_static_database_path),
            )
            stats = processor.process_all()
            stats["vision_items"] = [item.model_dump() for item in processor.inventory]
            stats["recognition_resources"] = processor.resources
            # 程序化调用也受完整性/资源门禁，部分解析成功不推进背包指针。
            commit_completed_vision_inventory(self.user_database_path, stats, [])
            logger.success("视觉解析完成")

    @timed("allocation.facade")
    def execute_allocation_inventory(
        self,
        inventory,
        priority_list,
        custom_sets=None,
        mode="role_priority",
        tape_main_filters=None,
        crit_priority_modes=None,
        set_effect_modes=None,
        priority_groups=None,
        crit_rate_caps=None,
        crit_rate_baselines=None,
        custom_weapons=None,
        locked_uids=None,
        blueprint_combo_limit: int = 2000,
        cancel_check=None,
        allocation_observer=None,
        static_read_unit=None,
        axis_selection=None,
        postprocess_budget=None,
        timings=None,
    ):
        """使用已经固定的数据集合计算，不要求生成中间库存文件。"""
        from src.services.allocation.axis.team_axis_selection import TeamAxisSelection
        if isinstance(axis_selection, TeamAxisSelection):
            from src.services.allocation.axis.team_enhancement import execute_team_enhancement
            return execute_team_enhancement(self, inventory, priority_list, axis_selection, mode=mode, options=dict(
                custom_sets=custom_sets, tape_main_filters=tape_main_filters, crit_priority_modes=crit_priority_modes,
                set_effect_modes=set_effect_modes, priority_groups=priority_groups, crit_rate_caps=crit_rate_caps,
                crit_rate_baselines=crit_rate_baselines, custom_weapons=custom_weapons, locked_uids=locked_uids,
                blueprint_combo_limit=blueprint_combo_limit, cancel_check=cancel_check,
                allocation_observer=allocation_observer, static_read_unit=static_read_unit, postprocess_budget=postprocess_budget,
                timings=timings))
        postprocess_options = {} if postprocess_budget is None else {
            "max_evaluations": postprocess_budget["max_evaluations"], "max_rounds": postprocess_budget["max_rounds"]}
        direct = mode == "direct_priority"
        axis = mode == "axis_priority"
        if axis and axis_selection is None:
            raise ValueError("轴策略需要明确选择本账号战报、范围、参与角色和A配置")
        selection_error = direct_strategy_selection_error(mode, priority_list)
        if selection_error:
            raise ValueError(selection_error)
        with timed_context(timings, "allocation.static_open", static_catalog_read_unit(self.allocation_static_database_path, read_unit=static_read_unit)) as unit, (
            UserDataDao.external_read_lease(self.user_database_path)
            if self.user_database_path.is_file() else nullcontext()
        ):
            require_allocation_release(unit.release, self.allocation_static_database_path)
            # 两阶段共用同一组件字节身份；基础分配仍走原路由与角色优先目标。
            # 所有配装模式共用本次已核验的分析组件；普通模式的图纸也走
            # 随附 allocation_blueprints_v1，不再让 Python DFS 成为默认路径。
            analysis_client = create_bundled_analysis_client(
                static_database_path=unit.release.database_path, cancelled=cancel_check,
            )
            session = getattr(type(analysis_client), "verified_session", None)
            with timed_context(timings, "allocation.component_lease", session(analysis_client, extra_paths=(unit.release.database_path, self.battle_semantics_path) if axis else ()) if session else nullcontext()):
                if axis:
                    from src.services.allocation.inputs.search_settings import search_budget
                    selected_axes = axis_selection if isinstance(axis_selection, tuple) else ((axis_selection, ()),)
                    modes = {selected.search_mode for selected, _ in selected_axes}
                    if len(modes) != 1:
                        raise ValueError("双队搜索档位不一致，请重新配置战报增强")
                    budget = search_budget(modes.pop())
                    if budget["max_millis"] > 120_000 and not analysis_client.supports_axis_deep_search:
                        raise ValueError("深搜需要新版分析组件，请先更新组件；可选择快搜继续")
                    analysis_client.timeout = (budget["max_millis"] + 1_000) / 1_000
                    def axis_checkpoint():
                        if cancel_check is not None and cancel_check():
                            from concurrent.futures import CancelledError
                            raise CancelledError("轴伤队伍预检已取消")
                    preflight = dict(user_database_path=self.user_database_path, static_read_unit=unit,
                        semantics_path=self.battle_semantics_path, source_inventory=inventory, checkpoint=axis_checkpoint)
                    if isinstance(axis_selection, tuple):
                        axis_selection = tuple((prepare_axis_team_selection(analysis_client, selected, roles, **preflight), roles)
                                               for selected, roles in axis_selection)
                        fixed = {uid for selected, _ in axis_selection for uid in selected.fixed_team_reserved_uids}
                    else:
                        axis_selection = prepare_axis_team_selection(analysis_client, axis_selection, priority_list, **preflight)
                        fixed = set(axis_selection.fixed_team_reserved_uids)
                    locked_uids = set(locked_uids or ()) | fixed
                    selections = axis_selection if isinstance(axis_selection, tuple) else ((axis_selection, tuple(priority_list)),)
                    if any(selected.rank_baseline for selected, _ in selections):
                        from src.services.allocation.axis.team_enhancement import ranked_team_baseline
                        priority_list, priority_groups, ranked = ranked_team_baseline(selections, priority_list)
                        axis_selection = ranked if isinstance(axis_selection, tuple) else ranked[0][0]
                native_allocation_executor = timed_call(timings, "allocation.native_prepare", create_allocation_executor,
                    static_database_path=unit.release.database_path,
                    cancel_check=cancel_check, analysis_client=analysis_client,
                )
                blueprint_executor = (
                    DeferredDamageBlueprints()
                    if direct or axis
                    else timed_call(
                        timings,
                        "allocation.blueprint_executor_prepare",
                        create_blueprint_executor,
                        analysis_client,
                        cancel_check=cancel_check,
                    )
                )

                def allocation_executor(request, scorer):
                    def checkpoint():
                        if cancel_check is not None and cancel_check():
                            from concurrent.futures import CancelledError
                            raise CancelledError("直伤后处理已取消")
                    frozen = timed_call(timings, "allocation.direct_inputs", freeze_direct_group_inputs,
                        request, user_database_path=self.user_database_path, static_read_unit=unit,
                        source_inventory=inventory, reserved_uids=locked_uids, checkpoint=checkpoint,
                    ) if direct else None
                    from src.services.allocation.axis.team_enhancement import freeze_joint_axes
                    freeze_axis = freeze_joint_axes if isinstance(axis_selection, tuple) else freeze_axis_allocation_inputs
                    axis_inputs = timed_call(timings, "allocation.axis_inputs", freeze_axis, analysis_client, request, axis_selection,
                        user_database_path=self.user_database_path, static_read_unit=unit,
                        source_inventory=inventory, semantics_path=self.battle_semantics_path,
                        reserved_uids=locked_uids, checkpoint=checkpoint) if axis else None
                    native_job = None
                    if direct or axis:
                        if axis and not axis_inputs.selection.optimize_groups:
                            from dataclasses import replace
                            axis_inputs = replace(axis_inputs, selection=replace(axis_inputs.selection, optimize_groups=True))
                        try:
                            request, plans, native_job = timed_call(timings, "allocation.damage_job", execute_damage_job,analysis_client, request, scorer,
                                axis_inputs if axis else frozen, blueprint_executor, unit, axis=axis,
                                checkpoint=checkpoint, timings=timings, **postprocess_options)
                        except NativeAnalysisCancelled:
                            from concurrent.futures import CancelledError
                            raise CancelledError("伤害分配已取消") from None
                    else:
                        plans = timed_call(timings, "allocation.native_baseline", native_allocation_executor, request, scorer)
                    if allocation_observer is not None:
                        allocation_observer(request, scorer)
                    baseline_plans = deepcopy(plans) if direct or axis else None
                    if direct:
                        plans = timed_call(timings, "allocation.direct_postprocess", optimize_standard_direct_groups,analysis_client, request, scorer, plans, frozen,
                                                          checkpoint=checkpoint, native_job=native_job)
                    if axis:
                        plans = timed_call(timings, "allocation.axis_postprocess", optimize_frozen_axis,analysis_client, request, scorer, plans, axis_inputs,
                                                     checkpoint=checkpoint, native_job=native_job)
                    if direct or axis:
                        plans = attach_baseline_fallbacks(plans, baseline_plans, axis_inputs if axis else frozen,
                            dataset_id=unit.release.dataset_id, input_identity=native_job.inner["input_identity"],
                            database_path=self.user_database_path, job_facts=native_job.facts)
                    return explain_allocation_failures(plans, request, scorer.stat_catalog)

                orchestrator = timed_call(timings, "allocation.catalog_prepare", NTEPipelineOrchestrator,
                    config_dir=self.config_dir,
                    user_database_path=self.user_database_path,
                    static_database_path=unit.release.database_path, static_read_unit=unit, timings=timings,
                )
                locked_uids = set(locked_uids or ())
                base_mode = "role_priority" if direct or axis else mode
                preferences_allowed = mode in ("role_priority", "update_mode", "direct_priority", "axis_priority")
                if mode == "update_mode":
                    with UserDataDao(self.user_database_path) as user_dao:
                        locked_uids.update({
                            f"nte-{'module' if row['kind'] == 'module' else 'core'}-"
                            f"{row['uid_slot']}-{row['uid_serial']}"
                            for row in user_dao.list_active_loadout_equipment_owners()
                        })
                    base_mode = "role_priority"
                if not preferences_allowed:
                    tape_main_filters = {}
                    crit_priority_modes = {}
                    crit_rate_caps = {}
                final_plan = orchestrator.run_full_allocation(
                    inventory=inventory,
                    priority_list=priority_list,
                    custom_sets=custom_sets or {},
                    mode=base_mode,
                    locked_uids=locked_uids,
                    tape_main_filters=tape_main_filters or {},
                    crit_priority_modes=crit_priority_modes or {},
                    set_effect_modes=set_effect_modes or {},
                    priority_groups=priority_groups,
                    crit_rate_caps=crit_rate_caps or {},
                    crit_rate_baselines=crit_rate_baselines or {},
                    custom_weapons=custom_weapons or {},
                    blueprint_combo_limit=blueprint_combo_limit,
                    cancel_check=cancel_check,
                    allocation_executor=allocation_executor,
                    blueprint_executor=blueprint_executor, timings=timings,
                )
                timed_call(timings, "allocation.static_revalidate", unit.ensure_unchanged)
                return final_plan, None

    def create_nte_core_client(self, **options) -> NteCoreClient:
        """创建一个尚未启动的 nte-core"""
        if self.log_dir is None or self.app_dir is None:
            raise ValueError("创建 nte-core 客户端必须显式提供日志目录和应用目录")
        options.setdefault(
            "data_dir",
            str(self.log_dir / "nte_core"),
        )
        options.setdefault("cwd", self.app_dir)
        options.setdefault("client_version", APP_VERSION)
        options.setdefault(
            "stderr_handler",
            lambda message: logger.debug(f"[nte-core] {message}"),
        )
        return NteCoreClient(**options)
