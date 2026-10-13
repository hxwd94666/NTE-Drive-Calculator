# 适配多角色冻结目标与准备缓存，共用养成后台任务生命周期。
from __future__ import annotations

from functools import partial
from src.features.toolbox.cultivation.calculation_controller import (
    CultivationCalculationController, CalculationSubmission,
)
from src.services.progression.batch_planner import (
    CultivationBatchRequest, CultivationBatchPlan, CultivationBatchPreparation,
)
from src.utils.cultivation_trace import trace_cultivation


class CultivationBatchController(CultivationCalculationController):
    def _make_read(self, request: CultivationBatchRequest, identity: object, kind: str):
        cached = self._preparation
        prepared = cached[1] if (cached is not None and cached[0] == identity
                                and cached[1].account_id == request.account_id
                                and cached[1].generation == request.generation
                                and cached[1].dataset_identity == request.dataset_identity
                                and cached[1].ordered_targets == request.ordered_targets) else None
        if kind == "prepare":
            return partial(prepare_batch, self._service, request, prepared)
        if prepared is not None:
            return partial(self._service.calculate, request, preparation=prepared)
        return partial(self._service.calculate, request)

    def _result_preparation(self, result: object, kind: str):
        prepared = result if kind == "prepare" else (
            result.preparation if isinstance(result, CultivationBatchPlan) else None
        )
        return prepared if isinstance(prepared, CultivationBatchPreparation) else None

    def _trace(self, submission: CalculationSubmission | None, event: str, **fields) -> None:
        trace_cultivation(submission.request.trace_id if submission else 0, event, **fields)


def prepare_batch(service, request, prepared):
    return prepared if prepared is not None else service.prepare(request)
