# 提供解析和分配流程的程序化门面。
"""Programmatic facade for allocation and vision processing."""

from __future__ import annotations

import json
import os

from src.app import runtime
from src.app.constants import APP_VERSION
from src.integrations.nte_core import NteCoreClient
from src.optimizer.state_manager import StateManager
from src.scanner.batch_processor import BatchProcessor
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.utils.logger import logger


class NTEAppFacade:
    def __init__(self, config_dir=None, user_config_dir=None):
        self.config_dir = config_dir or str(runtime.CONFIG_DIR)
        self.user_config_dir = user_config_dir or str(runtime.USER_CONFIG_DIR)

    def execute_vision_processing(self, input_dir=None, output_file=None):
        input_dir = input_dir or str(runtime.SCREENSHOT_DIR)
        output_file = output_file or str(runtime.OUTPUT_FILE)
        logger.info("开始视觉解析...")
        processor = BatchProcessor(
            input_dir=input_dir,
            output_file=output_file,
            config_dir=self.config_dir,
        )
        processor.process_all()
        logger.success("视觉解析完成")

    def execute_allocation(
        self,
        inventory_file,
        priority_list,
        custom_sets=None,
        mode="role_priority",
        tape_main_filters=None,
        crit_priority_modes=None,
        set_effect_modes=None,
        priority_groups=None,
        crit_rate_caps=None,
    ):
        if not os.path.exists(inventory_file):
            logger.error(f"找不到 {inventory_file}！")
            return None, None
        with open(inventory_file, "r", encoding="utf-8") as file:
            inventory = json.load(file)
        orchestrator = NTEPipelineOrchestrator(config_dir=self.config_dir)
        state_manager = StateManager(config_dir=self.user_config_dir)
        locked_uids = set()
        base_mode = mode
        preferences_allowed = mode in ("role_priority", "update_mode")
        if mode == "update_mode":
            locked_uids = state_manager.get_locked_uids()
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
        )
        return final_plan, state_manager

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
