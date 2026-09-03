# 提供解析和分配流程的程序化门面。
"""Programmatic facade for allocation and vision processing."""

from __future__ import annotations

from pathlib import Path

from src.app.constants import APP_VERSION
from src.integrations.nte_core import NteCoreClient
from src.scanner.batch_processor import BatchProcessor
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.vision_inventory_snapshot import import_vision_inventory
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
    ):
        self.config_dir = str(config_dir)
        self.user_config_dir = str(user_config_dir)
        self.user_database_path = Path(user_database_path)
        self.screenshot_dir = (
            Path(screenshot_dir) if screenshot_dir is not None else None
        )
        self.log_dir = Path(log_dir) if log_dir is not None else None
        self.app_dir = Path(app_dir) if app_dir is not None else None

    def execute_vision_processing(self, input_dir=None):
        resolved_input_dir = (
            Path(input_dir) if input_dir is not None else self.screenshot_dir
        )
        if resolved_input_dir is None:
            raise ValueError("视觉解析必须显式提供截图目录")
        logger.info("开始视觉解析...")
        processor = BatchProcessor(
            input_dir=str(resolved_input_dir),
            config_dir=self.config_dir,
        )
        processor.process_all()
        if processor.inventory:
            import_vision_inventory(
                self.user_database_path,
                [item.model_dump() for item in processor.inventory],
            )
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
    ):
        """使用已经固定的数据集合计算，不要求生成中间库存文件。"""

        orchestrator = NTEPipelineOrchestrator(
            config_dir=self.config_dir,
            user_database_path=self.user_database_path,
        )
        locked_uids = set(locked_uids or ())
        base_mode = mode
        preferences_allowed = mode in ("role_priority", "update_mode")
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
        )
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
