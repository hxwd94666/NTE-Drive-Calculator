# 固化基础权重页面使用的账号、代次、用户库和静态配置目录。

from __future__ import annotations

from src.app.context import AppContext
from src.domain.basic_weight import BasicWeightDependencies


def freeze_basic_weight_dependencies(app_context: AppContext) -> BasicWeightDependencies:
    """组合边界一次解析计算 dataset；领域与服务不依赖 Feature 或 AppContext。"""
    account = app_context.account
    return BasicWeightDependencies(
        account_id=account.active_account_id,
        generation=app_context.generation,
        user_database_path=account.user_database_path,
        config_dir=app_context.paths.config_dir,
        static_database_path=app_context.paths.equipment_allocation_database_path,
        shared_database_path=app_context.paths.shared_database_path,
    )
