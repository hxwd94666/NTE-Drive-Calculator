# 验证养成体力求解进程返回正式结果，并在子进程退出后可重新建立。
"""Windows process boundary contract for the cultivation MILP solver."""

from __future__ import annotations

import sys

import pytest

from src.domain.progression_stamina import (
    FarmingStage,
    MaterialRequirement,
    MaterialYield,
    ProgressionStaminaRequest,
    StaminaPlanStatus,
)
from src.services import cultivation_solver_process as solver


def _request() -> ProgressionStaminaRequest:
    return ProgressionStaminaRequest(
        hunter_level=60,
        requirements=(MaterialRequirement("test_material", 3),),
        stages=(FarmingStage(
            "test_stage", "测试副本", 1, 0, 20,
            (MaterialYield("test_material", 2),),
        ),),
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn boundary")
def test_stamina_solver_survives_helper_exit_and_restarts() -> None:
    try:
        first = solver.calculate_isolated_progression_stamina(_request())
        assert first.status == StaminaPlanStatus.COMPLETE
        assert first.total_stamina == 40
        prior = solver._process
        assert prior is not None
        prior.terminate()
        prior.join(timeout=5)

        second = solver.calculate_isolated_progression_stamina(_request())
        assert second == first
        assert solver._process is not prior
    finally:
        solver._shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn boundary")
def test_stamina_solver_invalid_request_keeps_value_error() -> None:
    invalid = ProgressionStaminaRequest(
        hunter_level=0,
        requirements=(),
        stages=(),
    )
    try:
        with pytest.raises(ValueError, match="猎人等级"):
            solver.calculate_isolated_progression_stamina(invalid)
    finally:
        solver._shutdown()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spawn boundary")
def test_stamina_solver_disconnect_does_not_publish_partial_result(monkeypatch) -> None:
    class BrokenConnection:
        def send(self, _payload) -> None:
            pass

        def poll(self, _timeout: float) -> bool:
            return True

        def recv(self):
            raise EOFError("native solver ended")

    dropped: list[bool] = []
    monkeypatch.setattr(solver, "_ensure_process", lambda: BrokenConnection())
    monkeypatch.setattr(solver, "_drop_process", lambda: dropped.append(True))

    with pytest.raises(RuntimeError, match="求解进程异常退出"):
        solver.calculate_isolated_progression_stamina(_request())
    assert dropped == [True]
