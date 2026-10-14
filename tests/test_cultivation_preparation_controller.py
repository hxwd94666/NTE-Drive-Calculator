# 验证提前材料准备与正式求解串行、有限缓存复用及过期目录隔离。
from __future__ import annotations

import os
from dataclasses import replace
from threading import Event

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NTE_TEST_TIER = "core"


def test_single_prepares_before_calculation_and_reuses_materials(tmp_path):
    from tests.test_cultivation_history_page import _page, _wait

    _app, page, history, planner = _page(tmp_path)
    calculator = page.calculator
    _wait(lambda: calculator.owned_materials.select_material("material-a"))
    assert planner.material_calls == 1
    assert planner.stamina_calls == 0
    assert history.list().total == 0
    assert not page.copy_button.isEnabled()
    calculator.owned_materials.restore_history_materials([
        {"item_id": "material-a", "quantity": 30, "manual_override": True,
         "source": "manual", "observed_at_utc": None},
    ])
    calculator._calculate()
    _wait(lambda: history.list().total == 1)
    assert planner.material_calls == 1
    assert planner.stamina_calls == 1
    calculator._target_level.setValue(70)
    assert not page.copy_button.isEnabled()
    _wait(lambda: calculator._prepared is not None and not calculator._controller.is_running())
    assert planner.material_calls == 2
    assert planner.stamina_calls == 1
    assert calculator.owned_materials.quantities()["material-a"] == 30
    page.shutdown()
    page.deleteLater()


def test_single_stale_preparation_is_discarded_and_new_context_has_no_cache():
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from src.features.toolbox.cultivation.single_controller import CultivationSingleController
    from tests.cultivation_planner_fixture import PlanningFixture
    from tests.test_cultivation_single_controller import _request, _wait_until

    class Planner(PlanningFixture):
        def __init__(self):
            self.started = Event()
            self.release = Event()
            self.calls = []

        def calculate(self, request):
            self.calls.append(request.character_id)
            if request.character_id == 1:
                self.started.set()
                assert self.release.wait(3)
            return super().calculate(request)

    app = QApplication.instance() or QApplication([])
    owner = QObject()
    identity = ["one"]
    planner = Planner()
    controller = CultivationSingleController(planner, context_identity=lambda: identity[0], parent=owner)
    prepared = []
    controller.preparation_ready.connect(prepared.append)
    controller.prepare(_request(1), "one")
    assert planner.started.wait(3)
    controller.prepare(_request(2), "one")
    planner.release.set()
    _wait_until(lambda: not controller.is_running())
    assert [item.request.character_id for item in prepared] == [2]
    assert planner.calls == [1, 2]
    identity[0] = "two"
    controller.prepare(_request(2), "two")
    _wait_until(lambda: not controller.is_running())
    assert planner.calls == [1, 2, 2]
    controller.close()
    owner.deleteLater()
    app.processEvents()


def test_batch_preparation_cache_ignores_stock_but_not_targets(monkeypatch):
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication
    from src.features.toolbox.cultivation.batch_controller import CultivationBatchController
    from src.services.progression.batch_planner import CultivationBatchPlannerService
    from tests.test_cultivation_batch_planner_service import _SingleService, _batch, _target
    from tests.test_cultivation_single_controller import _wait_until
    from tests.test_cultivation_history_page import _planner

    app = QApplication.instance() or QApplication([])
    owner = QObject()
    single = _SingleService()
    calls = []
    original = single.calculate

    def calculate(request):
        calls.append(request.character_id)
        return original(request)

    single.calculate = calculate
    total = _planner().calculate_stamina(None).total
    monkeypatch.setattr("src.services.progression.batch_planner.calculate_stamina_result", lambda *_a, **_k: total)
    controller = CultivationBatchController(CultivationBatchPlannerService(single), context_identity=lambda: "context", parent=owner)
    ready, results = [], []
    controller.preparation_ready.connect(ready.append)
    controller.result_ready.connect(results.append)
    request = _batch(_target(1), _target(2))
    controller.prepare(request, "context")
    _wait_until(lambda: not controller.is_running())
    assert calls == [1, 2] and len(ready) == 1 and not results
    controller.submit(replace(request, owned_quantities=(("a", 1),)), "context")
    _wait_until(lambda: not controller.is_running())
    assert calls == [1, 2] and len(results) == 1
    controller.prepare(_batch(_target(1)), "context")
    _wait_until(lambda: not controller.is_running())
    assert calls == [1, 2, 1]
    controller.invalidate(clear_preparation=True)
    controller.prepare(_batch(_target(1)), "context")
    _wait_until(lambda: not controller.is_running())
    assert calls == [1, 2, 1, 1]
    controller.close()
    owner.deleteLater()
    app.processEvents()
