# 验证提前准备材料不调用体力求解且正式计算可复用冻结需求。
from __future__ import annotations

from dataclasses import replace

import pytest

from src.services.progression.batch_planner import CultivationBatchPlannerService
from tests.test_cultivation_batch_planner_service import _SingleService, _batch, _target


NTE_TEST_TIER = "core"


def test_preparation_never_runs_stamina_solver(monkeypatch):
    calls = []
    single = _SingleService()
    calculate = single.calculate

    def counted(request):
        calls.append(request.character_id)
        return calculate(request)

    single.calculate = counted
    service = CultivationBatchPlannerService(single)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("preparation must not solve stamina")

    monkeypatch.setattr("src.services.progression.batch_planner.calculate_stamina_result", unexpected)
    prepared = service.prepare(_batch(_target(1), _target(2)))
    assert calls == [1, 2]
    assert prepared.merged_totals[0].quantity == 2
    assert "a" in prepared.stamina_item_ids
    assert prepared.ordered_targets == (_target(1), _target(2))


def test_mismatched_preparation_rejected_before_solver():
    service = CultivationBatchPlannerService(_SingleService())
    request = _batch(_target(1))
    prepared = service.prepare(request)
    with pytest.raises(ValueError):
        service.calculate(replace(request, dataset_identity="different"), preparation=prepared)
    with pytest.raises(ValueError):
        service.calculate(_batch(_target(2)), preparation=prepared)
