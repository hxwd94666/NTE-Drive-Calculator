# 分配策略旧入口兼容导出。
"""Legacy compatibility exports for allocation strategies.

Internal code and tests should import from the split strategy modules directly.
Keep this module logic-free so old external imports continue to work without
turning it back into a central strategy implementation.
"""

from src.optimizer.drive_candidate_ranker import BaseDispatchStrategy
from src.optimizer.blueprint_candidate_builder import BlueprintCandidateBuilder
from src.optimizer.allocation_matrix_builder import AllocationMatrixBuilder
from src.optimizer.role_priority_strategy import RolePriorityStrategy
from src.optimizer.drive_priority_strategy import MatrixBaseStrategy, DrivePriorityStrategy, GlobalOptimalStrategy
from src.optimizer.incremental_strategy import IncrementalStrategy

__all__ = [
    "BaseDispatchStrategy",
    "BlueprintCandidateBuilder",
    "AllocationMatrixBuilder",
    "RolePriorityStrategy",
    "MatrixBaseStrategy",
    "DrivePriorityStrategy",
    "GlobalOptimalStrategy",
    "IncrementalStrategy",
]
