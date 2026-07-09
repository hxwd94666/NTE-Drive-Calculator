# 增量更新策略入口，当前复用角色优先并由编排层保留已锁定装备。
from src.optimizer.role_priority_strategy import RolePriorityStrategy


class IncrementalStrategy(RolePriorityStrategy):
    """Explicit extension point for future incremental-update-specific allocation rules."""

    pass
