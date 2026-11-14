# 读取仓库独立编辑草稿和选择资料；规则生成只改返回草稿，不保存评分设置。
from concurrent.futures import CancelledError
from pathlib import Path
from traceback import extract_tb

from src.domain.equipment.warehouse.intelligent_rules import remembered_drive_grade
from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.observability import OperationContext, log_event
from src.observability.redaction import safe_exception
from src.services.identification.matching_editor import load_matching_editor
from src.services.inventory.management.management_choices import (
    load_drive_shape_options,
    load_set_name_options,
    load_role_options,
    load_role_selection_data,
)
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.services.inventory.recommendation.warehouse_rule_generation import generate_warehouse_rules


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
    context = OperationContext.create(
        "warehouse", account_id=request.account_id, context_generation=request.generation
    )
    stage = "context_check"
    try:
        check()
        grade = remembered_drive_grade(config)
        if grade is None:
            from src.services.inventory.recommendation.warehouse_quality_assessment import generate_first_rules

            stage = "initial_grade"
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
            stage = "regenerate"
            result = generate_warehouse_rules(request.static, config["preserve_rules"], grade_choice=grade)
        stage = "final_context_check"
        check()
        return result
    except CancelledError:
        raise
    except Exception as error:
        # 页面 worker 只转发展示文案；在异常链被折叠前保存受控诊断，不记账号草稿或装备正文。
        log_event(
            "ERROR", "warehouse.rules_failed", "仓库智能规则生成失败", context,
            phase="failed", stage=stage, error_chain=_rule_error_chain(error),
        )
        raise


def _rule_error_chain(error):
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 6:
        seen.add(id(error))
        entry = safe_exception(error)
        for name in ("code", "winerror", "errno", "sqlite_errorcode", "sqlite_errorname"):
            value = getattr(error, name, None)
            if isinstance(value, (str, int)):
                entry[name] = value
        frames = extract_tb(error.__traceback__)
        if frames:
            frame = frames[-1]
            entry["origin"] = f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
        chain.append(entry)
        error = error.__cause__ or (None if error.__suppress_context__ else error.__context__)
    return chain
