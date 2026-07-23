# 提供解析和分配流程的程序化门面。
"""Programmatic facade for allocation and vision processing."""

from __future__ import annotations

import os

from src.app import runtime
from src.app.constants import APP_VERSION
from src.integrations.nte_core import NteCoreClient
from src.scanner.batch_processor import BatchProcessor
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.storage.sqlite.user_data_dao import UserDataDao
from src.services.vision_inventory_snapshot import import_vision_inventory
from src.utils.logger import logger


class NTEAppFacade:
    def __init__(self, config_dir=None, user_config_dir=None, user_database_path=None):
        self.config_dir = config_dir or str(runtime.CONFIG_DIR)
        self.user_config_dir = user_config_dir or str(runtime.USER_CONFIG_DIR)
        self.user_database_path = user_database_path or runtime.USER_DATABASE_PATH

    def execute_vision_processing(self, input_dir=None):
        input_dir = input_dir or str(runtime.SCREENSHOT_DIR)
        logger.info("开始视觉解析...")
        processor = BatchProcessor(
            input_dir=input_dir,
            config_dir=self.config_dir,
        )
        processor.process_all()
        if processor.inventory:
            import_vision_inventory(
                runtime.USER_DATABASE_PATH,
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
        custom_weapons=None,
    ):
        """使用已经固定的数据集合计算，不要求生成中间库存文件。"""

        orchestrator = NTEPipelineOrchestrator(
            config_dir=self.config_dir,
            user_database_path=self.user_database_path,
        )
        locked_uids = set()
        base_mode = mode
        preferences_allowed = mode in ("role_priority", "update_mode")
        if mode == "update_mode":
            with UserDataDao(self.user_database_path) as user_dao:
                locked_uids = {
                    f"nte-{'module' if row['kind'] == 'module' else 'core'}-"
                    f"{row['uid_slot']}-{row['uid_serial']}"
                    for row in user_dao.list_active_loadout_equipment_owners()
                }
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
            custom_weapons=custom_weapons or {},
        )
        return final_plan, None

    def create_nte_core_client(self, **options) -> NteCoreClient:
        """创建一个尚未启动的 nte-core"""
        options.setdefault(
            "data_dir",
            os.path.join(runtime.LOG_DIR, "nte_core"),
        )
        options.setdefault("cwd", runtime.APP_DIR)
        options.setdefault("client_version", APP_VERSION)
        options.setdefault(
            "stderr_handler",
            lambda message: logger.debug(f"[nte-core] {message}"),
        )
        return NteCoreClient(**options)
