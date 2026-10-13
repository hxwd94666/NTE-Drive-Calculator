# 验证单角色计算在后台执行、末次结果胜出并按账号上下文丢弃旧结果。
from __future__ import annotations

import os
import time

from tests.cultivation_planner_fixture import PlanningFixture

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _wait_until(predicate, *, timeout: float = 3.0) -> None:
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert predicate()


def _request(character_id: int):
    from src.features.toolbox.cultivation.single_controller import SingleCalculationRequest
    from src.services.progression.planner_service import CultivationRequest

    return SingleCalculationRequest(
        CultivationRequest(character_id, 1, 0, 80, 6, ()), (), 60, 7,
    )


def test_single_controller_keeps_ui_responsive_and_only_publishes_latest() -> None:
    from PySide6.QtCore import QObject, QThread, QTimer
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation.single_controller import CultivationSingleController

    class Service(PlanningFixture):
        def calculate(self, request):
            time.sleep(0.08)
            return request.character_id

    QApplication.instance() or QApplication([])
    owner = QObject()
    controller = CultivationSingleController(
        Service(), context_identity=lambda: "account-a", parent=owner,
    )
    gui_thread = QThread.currentThread()
    timer_fired = []
    results = []
    controller.result_ready.connect(
        lambda result: results.append((result.plan, QThread.currentThread() == gui_thread))
    )
    controller.submit(_request(1), "account-a")
    QTimer.singleShot(0, lambda: timer_fired.append(True))
    controller.submit(_request(2), "account-a")
    _wait_until(lambda: not controller.is_running() and bool(results))

    assert timer_fired == [True]
    assert results == [(2, True)]
    controller.close()
    owner.deleteLater()


def test_single_controller_discards_changed_context_and_draft() -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation.single_controller import CultivationSingleController

    class Service(PlanningFixture):
        def calculate(self, request):
            time.sleep(0.05)
            return request.character_id

    QApplication.instance() or QApplication([])
    owner = QObject()
    context = {"value": "account-a"}
    controller = CultivationSingleController(
        Service(), context_identity=lambda: context["value"], parent=owner,
    )
    results = []
    controller.result_ready.connect(results.append)
    controller.submit(_request(1), "account-a")
    controller.invalidate()
    _wait_until(lambda: not controller.is_running())
    controller.submit(_request(2), "account-a")
    context["value"] = "account-b"
    _wait_until(lambda: not controller.is_running())

    assert results == []
    controller.close()
    owner.deleteLater()
