# 组合根显式构造轴选择窄服务，账号代次变化让旧选择失效。
from src.services.allocation.axis_selection import AxisAllocationSelectionService
from src.services.battle_report.persistence import BattleReportPersistenceDependencies


def build_axis_allocation_selection_service(app_context):
    account = app_context.account
    dependencies = BattleReportPersistenceDependencies(account.active_account_id, account.user_database_path,
        app_context.generation, app_context.paths.equipment_allocation_database_path)
    def current(frozen):
        return (app_context.generation == frozen.generation
            and app_context.account.active_account_id == frozen.account_id
            and app_context.account.user_database_path.resolve() == frozen.user_database_path.resolve()
            and app_context.paths.equipment_allocation_database_path.resolve() == frozen.static_database_path.resolve())
    return AxisAllocationSelectionService(dependencies=dependencies, context_is_current=current)
