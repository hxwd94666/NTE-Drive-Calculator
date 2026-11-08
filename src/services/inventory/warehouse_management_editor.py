# 读取仓库独立编辑草稿和选择资料；规则生成只改返回草稿，不保存评分设置。
from concurrent.futures import CancelledError

from src.domain.equipment.intelligent_rules import remembered_drive_grade
from src.domain.equipment.management_settings import MatchingScope
from src.services.identification.matching_editor import load_matching_editor
from src.services.inventory.management_choices import (
    load_drive_shape_options,
    load_set_name_options,
    load_role_options,
    load_role_selection_data,
)
from src.services.inventory.management_settings import ManagementSettingsService
from src.services.inventory.warehouse_rule_generation import generate_warehouse_rules


def load_warehouse_editor(request, cancel):
    service = ManagementSettingsService(request.database, scope=MatchingScope.WAREHOUSE)
    config, matching = service.load()
    data = load_matching_editor(request, cancel)
    choices = (
        load_drive_shape_options(request.static),
        load_set_name_options(request.static),
        load_role_options(request.database, request.static, request.asset_root),
        load_role_selection_data(request.database, request.static, request.asset_root),
    )
    if cancel.is_set():
        raise CancelledError()
    if service.load() != (config, matching) or data[2] != matching:
        raise ValueError("读取期间管理设置变化，请重新打开")
    return config, matching, data, choices


def generate_editor_rules(request, config, matching, cancel, check):
    check()
    grade = remembered_drive_grade(config)
    if grade is None:
        from src.services.inventory.warehouse_quality_assessment import generate_first_rules

        result = generate_first_rules(
            database=request.database,
            static_database=request.static,
            config_dir=request.config,
            config=config,
            matching=matching,
            check_context=check,
            cancel_check=cancel.is_set,
        )
    else:
        result = generate_warehouse_rules(request.static, config["preserve_rules"], grade_choice=grade)
    check()
    return result
