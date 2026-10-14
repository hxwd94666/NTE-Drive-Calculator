# 在后台计算单角色材料与体力，并丢弃过期的草稿结果。
"""One-worker owner for single-character cultivation calculations."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial

from src.features.toolbox.cultivation.calculation_controller import CultivationCalculationController
from src.domain.progression.history import HistoryCalculationEnvelope
from src.services.progression.planner_service import (
    CultivationPlan, CultivationPlannerService, CultivationRequest, CultivationStaminaPlan,
)
from src.services.progression.models import CultivationPreparedTarget


@dataclass(frozen=True, slots=True)
class SingleCalculationRequest:
    target: CultivationRequest
    owned_quantities: tuple[tuple[str, int], ...]
    hunter_level: int
    identification_level: int | None
    history_envelope: HistoryCalculationEnvelope | None = field(default=None, compare=False)


@dataclass(frozen=True, slots=True)
class SingleCalculationResult:
    request: SingleCalculationRequest
    plan: CultivationPlan
    stamina: CultivationStaminaPlan | None
    dataset_metadata: tuple[tuple[str, object], ...] = ()
    history_error: str | None = None
    preparation: CultivationPreparedTarget | None = None


def calculate_single(
    service: CultivationPlannerService, request: SingleCalculationRequest, prepared: CultivationPreparedTarget | None,
) -> SingleCalculationResult:
    metadata: tuple[tuple[str, object], ...] = ()
    history_error = None
    if request.history_envelope is not None:
        try:
            metadata = tuple(service.dataset_metadata().items())
        except Exception:
            history_error = "读取冻结资料身份失败，请重新计算。"
    prepared = prepared if prepared is not None else service.prepare(request.target)
    if prepared.request != request.target:
        raise ValueError("养成材料准备结果与冻结目标不一致")
    stamina = service.calculate_stamina(
        prepared.plan, owned_quantities=dict(request.owned_quantities),
        hunter_level=request.hunter_level,
        effective_identification_level=request.identification_level,
        farming_stages=prepared.farming_stages,
    )
    return SingleCalculationResult(request, prepared.plan, stamina, metadata, history_error, prepared)


class CultivationSingleController(CultivationCalculationController):
    def _make_read(self, request: SingleCalculationRequest, identity: object, kind: str):
        cached = self._preparation
        prepared = cached[1] if (cached is not None and cached[0] == identity
                                and cached[1].request == request.target) else None
        # 普通 Service 与冻结输入进入后台，QObject 与身份 provider 留在 GUI。
        if kind == "prepare":
            return partial(prepare_single, self._service, request.target, prepared)
        return partial(calculate_single, self._service, request, prepared)

    def _result_preparation(self, result: object, kind: str):
        prepared = result if kind == "prepare" else getattr(result, "preparation", None)
        return prepared if isinstance(prepared, CultivationPreparedTarget) else None


def prepare_single(service, target, prepared):
    return prepared if prepared is not None else service.prepare(target)
