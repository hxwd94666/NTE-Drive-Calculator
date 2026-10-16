# 提供解析和分配流程的程序化门面。
"""Programmatic facade for allocation and vision processing."""

from __future__ import annotations

from pathlib import Path

from src.app.constants import APP_VERSION
from src.integrations.nte_core import NteCoreClient
from src.integrations.bundled_resources import bundled_config_dir
from src.integrations.static_catalog_release import static_catalog_read_unit, StaticCatalogReleaseReader
from src.services.allocation.plan_save import require_allocation_release
from src.integrations.native_allocation import create_allocation_executor
from src.integrations.nte_analysis_core import NativeAnalysisCancelled
from src.integrations.damage_allocation_job import DeferredDamageBlueprints, execute_damage_job
from src.integrations.analysis_core_release import create_bundled_analysis_client
from src.services.allocation.direct_postprocess import direct_strategy_selection_error
from src.services.allocation.direct_group_postprocess import freeze_direct_group_inputs, optimize_standard_direct_groups
from src.services.allocation.axis_team_preflight import prepare_axis_team_selection
from src.services.allocation.axis_postprocess import freeze_axis_allocation_inputs, optimize_frozen_axis
from src.integrations.vision.recognition.batch_processor import BatchProcessor
from src.services.recognition.resources import load_recognition_resources
from src.domain.allocation.failures import explain_allocation_failures
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.storage.sqlite.user_data_dao import UserDataDao
from src.integrations.vision.capture_files import capture_output_lease
from src.services.inventory.visual_scan_commit import commit_completed_vision_inventory
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
    ):
        """使用已经固定的数据集合计算，不要求生成中间库存文件。"""
        postprocess_options = {} if postprocess_budget is None else {
            "max_evaluations": postprocess_budget["max_evaluations"], "max_rounds": postprocess_budget["max_rounds"]}
        direct = mode == "direct_priority"
        axis = mode == "axis_priority"
        if axis and axis_selection is None:
            raise ValueError("轴策略需要明确选择本账号战报、范围、参与角色和A配置")
        selection_error = direct_strategy_selection_error(mode, priority_list)
        if selection_error:
            raise ValueError(selection_error)
        with static_catalog_read_unit(self.allocation_static_database_path, read_unit=static_read_unit) as unit:
            require_allocation_release(unit.release, self.allocation_static_database_path)
            # 两阶段共用同一组件字节身份；基础分配仍走原路由与角色优先目标。
            analysis_client = create_bundled_analysis_client(
                static_database_path=unit.release.database_path, cancelled=cancel_check,
            ) if direct or axis else None
            if axis:
                def axis_checkpoint():
                    if cancel_check is not None and cancel_check():
                        from concurrent.futures import CancelledError
                        raise CancelledError("轴伤队伍预检已取消")
                axis_selection = prepare_axis_team_selection(analysis_client, axis_selection, priority_list,
                    user_database_path=self.user_database_path, static_read_unit=unit,
                    semantics_path=self.battle_semantics_path, source_inventory=inventory, checkpoint=axis_checkpoint)
                locked_uids = set(locked_uids or ()) | set(axis_selection.fixed_team_reserved_uids)
            native_allocation_executor = create_allocation_executor(
                static_database_path=unit.release.database_path,
                cancel_check=cancel_check, analysis_client=analysis_client,
            )
            blueprint_executor = DeferredDamageBlueprints() if direct or axis else None

            def allocation_executor(request, scorer):
                def checkpoint():
                    if cancel_check is not None and cancel_check():
                        from concurrent.futures import CancelledError
                        raise CancelledError("直伤后处理已取消")
                frozen = freeze_direct_group_inputs(
                    request, user_database_path=self.user_database_path, static_read_unit=unit,
                    source_inventory=inventory, reserved_uids=locked_uids, checkpoint=checkpoint,
                ) if direct else None
                axis_inputs = freeze_axis_allocation_inputs(analysis_client, request, axis_selection,
                    user_database_path=self.user_database_path, static_read_unit=unit,
                    source_inventory=inventory, semantics_path=self.battle_semantics_path,
                    reserved_uids=locked_uids, checkpoint=checkpoint) if axis else None
                native_job = None
                if direct or axis:
                    if axis and not axis_inputs.selection.optimize_groups:
                        from dataclasses import replace
                        axis_inputs = replace(axis_inputs, selection=replace(axis_inputs.selection, optimize_groups=True))
                    try:
                        request, plans, native_job = execute_damage_job(analysis_client, request, scorer,
                            axis_inputs if axis else frozen, blueprint_executor, unit, axis=axis,
                            checkpoint=checkpoint, **postprocess_options)
                    except NativeAnalysisCancelled:
                        from concurrent.futures import CancelledError
                        raise CancelledError("伤害分配已取消") from None
                else:
                    plans = native_allocation_executor(request, scorer)
                if allocation_observer is not None:
                    allocation_observer(request, scorer)
                if direct:
                    plans = optimize_standard_direct_groups(analysis_client, request, scorer, plans, frozen,
                                                      checkpoint=checkpoint, native_job=native_job)
                if axis:
                    plans = optimize_frozen_axis(analysis_client, request, scorer, plans, axis_inputs,
                                                 checkpoint=checkpoint, native_job=native_job)
                return explain_allocation_failures(plans, request, scorer.stat_catalog)

            orchestrator = NTEPipelineOrchestrator(
                config_dir=self.config_dir,
                user_database_path=self.user_database_path,
                static_database_path=unit.release.database_path, static_read_unit=unit,
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
                blueprint_executor=blueprint_executor,
            )
            StaticCatalogReleaseReader().ensure_unchanged(unit.release)
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
