# 验证加权分配公开入口观测正式未分配角色并原样交付冻结结果。
from contextlib import contextmanager
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.weighted_allocation import runner
from src.observability import OperationContext
from src.services.allocation_solver import AllocationSolveResult, UnifiedAllocation

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("unassigned", [(), (101, 102)])
def test_public_facade_observes_real_result_without_replacing_it(tmp_path, unassigned):
    database = tmp_path / "synthetic-user.fixture"
    database.write_bytes(b"synthetic facade guard only")
    operation = OperationContext(feature="allocation", operation_id="facade-fixture", snapshot_id=1)
    request = runner.WeightedAllocationRequest(
        user_database_path=database, snapshot_id=1, profile_id=1, profile_version=1,
        top_k=1, operation_context=operation,
    )
    unified = UnifiedAllocation(
        strategy="role_priority", total_score=0.0, selected=(),
        unassigned_character_ids=unassigned, explanation=(),
    )
    result = AllocationSolveResult(
        snapshot_id=1, profile_id=1, profile_version=1, solver_version="facade-fixture",
        top_k=1, role_top_k=(), unified=unified,
    )
    before = asdict(result)
    preview = SimpleNamespace(context=SimpleNamespace(roles=(101, 102), candidates=()), result=result)
    observations = []

    @contextmanager
    def scope(actual_operation, **_values):
        assert actual_operation is operation
        yield SimpleNamespace(annotate=lambda **values: observations.append(values))

    # 仅替换内部求解/日志owner；公开请求与正式结果类型仍经过真实入口。
    with patch.object(runner, "operation_scope", scope), patch.object(
        runner, "_run_weighted_allocation", return_value=preview,
    ) as compute:
        returned = runner.run_weighted_allocation(request)
    compute.assert_called_once_with(request, operation)
    assert returned is preview and returned.result is result
    assert asdict(result) == before
    assert observations == [{
        "role_count": 2, "candidate_count": 0, "selected_role_count": 0,
        "unassigned_role_count": len(unassigned), "allocation_strategy": "role_priority",
    }]
