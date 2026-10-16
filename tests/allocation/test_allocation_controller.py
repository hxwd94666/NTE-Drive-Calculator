# 验证迁移后的单一分配Controller仍冻结启动身份并撤销本次取消令牌，不读取真实账号或游戏。
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
import pytest

from src.features.allocation.controller import AllocationController
from src.services.allocation.filter_settings import AllocationFilterSettings


NTE_TEST_TIER = "core"


@pytest.fixture
def controller():
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    context = SimpleNamespace(generation=4, account=SimpleNamespace(active_account_id="fixture", user_database_path=Path("fixture.sqlite3")))
    owner = AllocationController(app_context=context, dialog_parent=parent, equipment_presentation=Mock(),
        preferences_provider=lambda: {}, save_preferences=Mock(), refresh_roles=Mock(), refresh_equipment=Mock(), hotkey_manager=Mock())
    owner.bind_run_button(QPushButton(parent))
    yield owner
    owner.begin_close()
    parent.close()
    parent.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def start(owner, *, strategy="role_priority", axis_selection=None):
    owner.start(strategy=strategy, selected_roles=["fixture-role"], custom_sets={}, tape_main_filters={},
        crit_priority_modes={}, set_effect_modes={}, priority_groups=[["fixture-role"]], crit_rate_caps={},
        crit_rate_baselines={}, custom_weapons={}, filter_settings=AllocationFilterSettings(), axis_selection=axis_selection)


def test_start_freezes_account_generation_and_owns_stop_callback(controller):
    with patch("src.features.allocation.controller._start_allocation_worker") as worker:
        start(controller)
    worker.assert_called_once_with(controller)
    assert controller._pending_allocation_context_identity == (4, "fixture", Path("fixture.sqlite3"))
    assert controller._pending_run_id == 1
    controller._hotkey_manager.start.assert_called_once_with(owner="allocation", on_stop=controller.cancel)


def test_cancel_old_token_does_not_poison_next_start(controller):
    with patch("src.features.allocation.controller._start_allocation_worker"):
        start(controller)
        old = controller._cancel_event
        controller.cancel()
        assert old.is_set()
        start(controller)
    assert old.is_set() and not controller._cancel_event.is_set()
    assert controller._pending_run_id == 2


def test_begin_close_rejects_new_worker_and_reset_releases_only_allocation_owner(controller):
    controller.reset_account_state()
    controller._hotkey_manager.stop.assert_called_once_with(owner="allocation")
    controller.begin_close()
    with patch("src.features.allocation.controller._start_allocation_worker") as worker, pytest.raises(RuntimeError, match="关闭"):
        start(controller)
    worker.assert_not_called()


def test_worker_receives_frozen_axis_selection_and_its_own_cancel_token(controller):
    selection = {"report": 7, "range": [500_000, 1_000_000], "anchor": "original"}
    controller._run_allocation = Mock()
    with patch("src.features.allocation.runner.WorkerThread.start"):
        start(controller, strategy="axis_priority", axis_selection=selection)
    selection["range"][0] = 9_000_000
    controller._pending_axis_selection = {"report": 99}
    controller.cancel()
    controller._worker.target()
    kwargs = controller._run_allocation.call_args.kwargs
    assert kwargs["axis_selection"] == {"report": 7, "range": [500_000, 1_000_000], "anchor": "original"}
    assert kwargs["cancel_check"]() and kwargs["expected_run_id"] == 1
