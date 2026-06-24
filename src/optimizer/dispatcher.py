# 分配算法的调度入口。
"""Dispatch facade that selects the requested allocation strategy."""

from src.optimizer.strategies import RolePriorityStrategy, DrivePriorityStrategy, GlobalOptimalStrategy
from src.optimizer.contracts import (
    AllocationResult,
    CandidatePool,
    CustomSetMap,
    StatPriorityConfigMap,
    STRATEGY_MODES,
    StrategyMode,
)


class DispatcherEngine:

    def __init__(self, roles_db: dict, sets_db: dict, blueprints_db: dict[str, list[dict]]):
        self.strategies = {
            "role_priority": RolePriorityStrategy(roles_db, sets_db, blueprints_db),
            "drive_priority": DrivePriorityStrategy(roles_db, sets_db, blueprints_db),
            "global_optimal": GlobalOptimalStrategy(roles_db, sets_db, blueprints_db)
        }

    def execute_dispatch(
        self,
        mode: StrategyMode | str,
        candidate_pool: CandidatePool,
        priority_list: list[str],
        custom_sets: CustomSetMap = None,
        crit_priority_modes: StatPriorityConfigMap = None,
        priority_groups: list[list[str]] | None = None,
    ) -> AllocationResult:
        custom_sets = custom_sets or {}
        crit_priority_modes = crit_priority_modes or {}
        strategy = self.strategies.get(mode)

        if not strategy:
            raise ValueError(f"未知的调度模式 [{mode}]，支持的模式: {list(STRATEGY_MODES)}")

        if mode == "role_priority":
            return strategy.execute(
                candidate_pool,
                priority_list,
                custom_sets,
                crit_priority_modes,
                priority_groups=priority_groups,
            )
        crit_priority_modes = {}
        return strategy.execute(candidate_pool, priority_list, custom_sets, crit_priority_modes)
