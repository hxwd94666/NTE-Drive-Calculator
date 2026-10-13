# 显式组装工具页的账号服务、历史存储及窄导航依赖。
from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING

from src.app.context import AppContext, CallbackAccountLifecycle
from src.features.toolbox.toolbox_navigation import ToolboxDependencies, cultivation_context_identity
from src.services.progression.history_service import CultivationHistoryService
from src.services.progression.owned_material_import import ImportedOwnedMaterials
from src.services.progression.planner_service import CultivationPlannerService
from src.services.rewind.recommendation import RewindShapeRecommendationService


if TYPE_CHECKING:
    from src.features.toolbox.page import ToolboxPage


def build_toolbox_dependencies(
    context: AppContext, *, operation_entry: Callable[..., Any], operation_unavailable: Callable[..., Any],
    operation_guard: Callable[..., Any], operation_generation: Callable[[], object],
    material_importer: Callable[[], ImportedOwnedMaterials], navigate_static_catalog: Callable[[], None],
) -> ToolboxDependencies:
    def identity():
        return cultivation_context_identity(context.account.active_account_id, context.generation,
                                            context.paths.cultivation_database_path)

    return ToolboxDependencies(
        operation_entry=operation_entry, operation_unavailable=operation_unavailable,
        operation_guard=operation_guard, operation_generation=operation_generation,
        rewind_service_factory=lambda: RewindShapeRecommendationService(
            user_database_path=context.account.user_database_path,
            static_database_path=context.paths.equipment_allocation_database_path,
            asset_root=context.paths.equipment_allocation_asset_root,
        ),
        cultivation_service_factory=lambda: CultivationPlannerService(
            user_database_path=context.account.user_database_path,
            static_database_path=context.paths.cultivation_database_path,
        ),
        cultivation_history_service_factory=lambda: CultivationHistoryService(
            account_id=str(context.account.active_account_id), user_database_path=context.account.user_database_path,
            context_identity=identity,
        ),
        cultivation_material_importer=material_importer, cultivation_context_identity=identity,
        cultivation_asset_root=lambda: context.paths.cultivation_asset_root,
        navigate_static_catalog=navigate_static_catalog,
    )


def register_toolbox_account_lifecycle(context: AppContext, toolbox: ToolboxPage) -> Callable[[], None]:
    """账号身份替换前撤销隐藏养成页；旧物理任务仍由原页面树保留。"""
    # 工具页是用户打开的草稿，不是切换后自动恢复的后台服务。
    return context.register_account_lifecycle(CallbackAccountLifecycle(
        is_running=lambda: False, stop=toolbox.shutdown,
        rebuild=lambda _account: None, start=lambda: None,
    ))
