# 测试工具页倒带推荐、执行配置和候选驱动交互。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_rewind_execution_dialog_accept_persists_and_reopens_account_options(
    monkeypatch,
) -> None:
    from PySide6.QtWidgets import QApplication, QDialog

    from src.features.toolbox import rewind_execution_ui
    from src.features.toolbox.page import _RewindRecommendationDialog
    from src.features.toolbox.rewind_execution_dialog import RewindExecutionOptions

    class Service:
        def __init__(self) -> None:
            self.saved = {"target_character_ids": [1004]}

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

    class AcceptedDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def options(self):
            return RewindExecutionOptions(
                qualities=("purple", "gold"),
                drive_customization="enabled",
            )

    QApplication.instance() or QApplication([])
    service = Service()
    dialog = _RewindRecommendationDialog(service, None)
    monkeypatch.setattr(rewind_execution_ui, "RewindExecutionDialog", AcceptedDialog)
    monkeypatch.setattr(dialog, "_start_rewind_execution", lambda: None)

    dialog._configure_rewind()

    assert service.saved["target_character_ids"] == [1004]
    assert service.saved["main_character_ids"] == []
    assert service.saved["strategy"] == "balanced"
    assert service.saved["target_grade"] == "S"
    assert service.saved["rewind_qualities"] == ["purple", "gold"]
    assert service.saved["rewind_drive_customization"] == "enabled"
    reopened = _RewindRecommendationDialog(service, None)
    assert reopened._rewind_options == RewindExecutionOptions(
        qualities=("purple", "gold"),
        drive_customization="enabled",
    )


def test_rewind_execution_dialog_marks_experimental_prerequisite_and_disables_custom_for_blue_only() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from src.features.toolbox.rewind_execution_dialog import (
        RewindExecutionDialog,
        RewindExecutionOptions,
    )

    QApplication.instance() or QApplication([])
    dialog = RewindExecutionDialog(
        initial=RewindExecutionOptions(("blue",), "apply_plan")
    )
    descriptions = {label.text() for label in dialog.findChildren(QLabel)}
    custom_buttons = {
        str(button.property("rewindValue")): button
        for button in dialog.findChildren(QPushButton, "rewindCustomizationTile")
    }

    assert any("提前打开游戏内的倒带页面" in text for text in descriptions)
    assert any("实验性开发" in text and "不保证可以使用" in text for text in descriptions)
    notice = dialog.findChild(QLabel, "rewindExperimentalNotice")
    assert notice is not None
    assert "background:#1f6feb33" in notice.styleSheet()
    assert "color:#58a6ff" in notice.styleSheet()
    assert dialog.options().drive_customization == "none"
    assert custom_buttons["none"].isChecked()
    assert not custom_buttons["enabled"].isEnabled()
    assert not custom_buttons["apply_plan"].isEnabled()

    purple = dialog.findChildren(QPushButton, "rewindQualityTile")[1]
    purple.click()

    assert custom_buttons["enabled"].isEnabled()
    assert custom_buttons["apply_plan"].isEnabled()


def test_rewind_execution_minimizes_host_and_restores_after_completion() -> None:
    from PySide6.QtWidgets import QApplication, QWidget

    from src.features.drive_assembly.rewind_execution import RewindExecutionReport
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def load_preferences(self):
            return {}

    QApplication.instance() or QApplication([])
    host = QWidget()
    host.show()
    dialog = _RewindRecommendationDialog(Service(), host)
    dialog.show()

    dialog._prepare_rewind_game_foreground()

    assert dialog.isHidden()
    assert host.isMinimized()

    dialog._on_rewind_complete(RewindExecutionReport(1850, 30, 3, 0, 5, "difficulty_low", (), 50))

    assert not dialog.isHidden()
    assert not host.isMinimized()
    assert dialog._start_rewind_button.text() == "倒带完成 30 次，剩余 50"

    dialog._on_rewind_complete(RewindExecutionReport(
        3000,
        50,
        5,
        0,
        9,
        "difficulty_advanced",
        (("gold", 30), ("blue", 20)),
        70,
        (("gold", 1850), ("blue", 1220)),
        (("gold", 50), ("blue", 20)),
    ))

    assert dialog._start_rewind_button.text() == "倒带完成 50 次，金剩50、蓝剩20"


def test_rewind_execution_replaces_deleted_worker_and_clears_finished_reference(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox import rewind_execution_ui
    from src.features.toolbox.rewind_execution_dialog import RewindExecutionOptions

    class DeletedWorker:
        def isRunning(self):
            raise RuntimeError("libshiboken: Internal C++ object already deleted")

    class Signal:
        def __init__(self) -> None:
            self.callbacks = []

        def connect(self, callback) -> None:
            self.callbacks.append(callback)

        def emit(self) -> None:
            for callback in tuple(self.callbacks):
                callback()

    class FakeWorker:
        def __init__(self, target, parent=None) -> None:
            self.target = target
            self.parent = parent
            self.result_ready = Signal()
            self.error = Signal()
            self.finished = Signal()
            self.started = False
            self.deleted = False

        def start(self) -> None:
            self.started = True

        def deleteLater(self) -> None:
            self.deleted = True

    class Host(rewind_execution_ui.RewindExecutionUiMixin):
        def __init__(self) -> None:
            self._rewind_worker = DeletedWorker()
            self._rewind_options = RewindExecutionOptions(("purple",), "none")
            self._saved_rewind_shape_ids = ()
            self._start_rewind_button = QPushButton()

        def _prepare_rewind_game_foreground(self) -> None:
            pass

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(rewind_execution_ui, "WorkerThread", FakeWorker)
    host = Host()

    host._start_rewind_execution()

    worker = host._rewind_worker
    assert isinstance(worker, FakeWorker)
    assert worker.started
    worker.finished.emit()
    assert host._rewind_worker is None
    assert worker.deleted


def test_rewind_open_prefers_saved_plan_and_replacement_has_all_twelve_shapes() -> None:
    from PySide6.QtWidgets import QApplication, QSpinBox, QToolButton

    from src.features.toolbox.page import _RewindRecommendationDialog
    from src.features.toolbox.rewind_execution_dialog import RewindShapeReplacementDialog
    from src.features.toolbox.rewind_slot_ui import all_rewind_shape_candidates

    class Service:
        def load_preferences(self):
            return {"saved_rewind_shape_ids": ["EquipmentGeometry_Hen2"] * 8}

    QApplication.instance() or QApplication([])
    dialog = _RewindRecommendationDialog(Service(), None)
    assert dialog._slots_complete()
    assert all(slot.shape.shape_id == "EquipmentGeometry_Hen2" for slot in dialog._editable_slots)
    picker = RewindShapeReplacementDialog(None, candidates=all_rewind_shape_candidates())
    assert len(picker.findChildren(QToolButton, "rewindShapeReplacementOption")) == 12
    assert not picker.findChildren(QSpinBox)


def test_saved_rewind_plan_restores_quality_gap_without_another_analysis() -> None:
    from PySide6.QtWidgets import QApplication, QLabel

    from src.domain.rewind_shape_recommendation import RewindShape, RewindShapeRecommendation
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def __init__(self) -> None:
            self.saved: dict[str, object] = {}

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

    QApplication.instance() or QApplication([])
    service = Service()
    dialog = _RewindRecommendationDialog(service, None)
    saved = RewindShapeRecommendation(
        RewindShape("EquipmentGeometry_Hen2", 2),
        suit_demand=1,
        owned_count=25,
        priority_score=15.0,
        quality_gap=12.5,
    )
    dialog._editable_slots = [saved] * 8
    dialog._save_plan()

    assert service.saved["saved_rewind_shape_ids"] == ["EquipmentGeometry_Hen2"] * 8
    assert service.saved["saved_rewind_slots"] == [
        {"shape_id": "EquipmentGeometry_Hen2", "quality_gap": 12.5}
    ] * 8

    reopened = _RewindRecommendationDialog(service, None)
    assert reopened._slots_complete()
    assert [slot.quality_gap for slot in reopened._editable_slots if slot is not None] == [
        12.5
    ] * 8
    assert all(
        "缺分 12.5" in label.text()
        for label in reopened.findChildren(QLabel, "rewindShapeMetrics")
    )


def test_rewind_candidates_clear_only_the_current_page_and_keep_the_saved_plan() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def __init__(self) -> None:
            self.saved = {"saved_rewind_shape_ids": ["EquipmentGeometry_Hen2"] * 8}

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

    QApplication.instance() or QApplication([])
    service = Service()
    dialog = _RewindRecommendationDialog(service, None)
    clear_button = dialog.findChild(QPushButton, "rewindClearCandidates")

    assert clear_button is not None
    assert clear_button.isEnabled()
    clear_button.click()

    assert not any(dialog._editable_slots)
    assert service.saved["saved_rewind_shape_ids"] == ["EquipmentGeometry_Hen2"] * 8
    assert dialog._saved_rewind_shape_ids == ("EquipmentGeometry_Hen2",) * 8

    reopened = _RewindRecommendationDialog(service, None)
    assert reopened._slots_complete()
    assert all(
        slot.shape.shape_id == "EquipmentGeometry_Hen2"
        for slot in reopened._editable_slots
    )


def test_generating_another_strategy_replaces_the_current_transient_slots() -> None:
    from PySide6.QtWidgets import QApplication

    from src.domain.rewind_shape_recommendation import RewindShape, RewindShapeRecommendation
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def load_preferences(self):
            return {}

    QApplication.instance() or QApplication([])
    dialog = _RewindRecommendationDialog(Service(), None)
    balanced = RewindShapeRecommendation(
        RewindShape("EquipmentGeometry_Hen2", 2),
        suit_demand=1,
        owned_count=1,
        priority_score=4.0,
        quantity=8,
        quality_gap=4.0,
    )
    focused = RewindShapeRecommendation(
        RewindShape("EquipmentGeometry_Hen3", 3),
        suit_demand=1,
        owned_count=1,
        priority_score=9.0,
        quantity=8,
        quality_gap=9.0,
    )

    dialog._apply_recommendations((balanced,))
    dialog._apply_recommendations((focused,))

    assert all(
        slot is not None and slot.shape.shape_id == "EquipmentGeometry_Hen3"
        for slot in dialog._editable_slots
    )
