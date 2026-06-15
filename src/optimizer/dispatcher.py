"""Dispatch facade that selects the requested allocation strategy."""

from typing import List, Dict, Any
from src.optimizer.strategies import RolePriorityStrategy, DrivePriorityStrategy, GlobalOptimalStrategy


class DispatcherEngine:

    def __init__(self, roles_db: Dict, sets_db: Dict, blueprints_db: Dict[str, List[Dict]]):
        self.strategies = {
            "role_priority": RolePriorityStrategy(roles_db, sets_db, blueprints_db),
            "drive_priority": DrivePriorityStrategy(roles_db, sets_db, blueprints_db),
            "global_optimal": GlobalOptimalStrategy(roles_db, sets_db, blueprints_db)
        }

    def execute_dispatch(self, mode: str, candidate_pool: Dict[str, Any], priority_list: List[str],
                         custom_sets: Dict[str, str] = None,
                         crit_priority_modes: Dict[str, str] = None) -> Dict[str, Any]:
        custom_sets = custom_sets or {}
        crit_priority_modes = crit_priority_modes or {}
        strategy = self.strategies.get(mode)

        if not strategy:
            raise ValueError(f"未知的调度模式 [{mode}]，支持的模式: {list(self.strategies.keys())}")

        return strategy.execute(candidate_pool, priority_list, custom_sets, crit_priority_modes)
