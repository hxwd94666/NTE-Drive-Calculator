# 测试工具页倒带推荐、执行配置和候选驱动交互。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_rewind_control_mapping_scales_every_click_and_ocr_region_for_1080p_2k_4k() -> None:
    from src.features.drive_assembly.page_navigation_mapping import map_rewind_controls

    reference = map_rewind_controls(screen_size=(2560, 1440))
    for screen_size, scale in (
        ((1920, 1080), 0.75),
        ((2560, 1440), 1.0),
        ((3840, 2160), 1.5),
    ):
        controls = map_rewind_controls(screen_size=screen_size)

        def scaled(point: tuple[int, int]) -> tuple[int, int]:
            return tuple(int(value * scale + 0.5001) for value in point)

        for name, value in reference.items():
            if name.endswith("_region"):
                expected = (*scaled(value[:2]), *scaled(value[2:]))
                assert controls[name] == expected
            elif name == "selected_drive_remove":
                assert controls[name] == [scaled(point) for point in value]
            elif name == "available_drive_shapes":
                assert controls[name] == {
                    shape: scaled(point) for shape, point in value.items()
                }
            else:
                assert controls[name] == scaled(value)


def test_rewind_execution_options_expose_tile_multiselect_choices() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from src.features.toolbox.rewind_execution_dialog import RewindExecutionDialog

    QApplication.instance() or QApplication([])
    dialog = RewindExecutionDialog()
    labels = {button.text() for button in dialog.findChildren(QPushButton)}
    descriptions = {label.text() for label in dialog.findChildren(QLabel)}

    assert {"蓝色品质", "紫色品质", "金色品质"}.issubset(labels)
    assert {"否", "是且不做更改", "是且应用方案"}.issubset(labels)
    assert not any("每个品质先切换" in text for text in descriptions)
    assert dialog.options().qualities == ("gold",)
    assert dialog.options().drive_customization == "none"


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


def test_rewind_tiles_use_the_dark_recommendation_selection_style_and_customization_help(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

    from src.features.toolbox.rewind_execution_dialog import RewindExecutionDialog

    QApplication.instance() or QApplication([])
    captured: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: captured.append((title, text)),
    )
    dialog = RewindExecutionDialog()
    quality = dialog.findChild(QPushButton, "rewindQualityTile")
    help_button = dialog.findChild(QPushButton, "rewindCustomizationHelp")

    assert quality is not None
    assert "background:#21262d" in quality.styleSheet()
    assert "background:#1f6feb33" in quality.styleSheet()
    assert help_button is not None
    help_button.click()
    assert captured and all(label in captured[0][1] for label in ("否", "是且不做更改", "是且应用方案"))

def test_rewind_result_slots_are_editable_and_enable_plan_actions() -> None:
    from PySide6.QtWidgets import QApplication, QFrame, QLabel

    from src.domain.rewind_shape_recommendation import RewindShape, RewindShapeRecommendation
    from src.features.toolbox.page import _RewindRecommendationDialog
    from src.services.rewind_shape_recommendation_service import RewindShapeAnalysis

    class Service:
        saved: dict[str, object] = {}

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

    QApplication.instance() or QApplication([])
    first = RewindShapeRecommendation(
        RewindShape("shape_a", 2),
        1,
        7,
        1.0,
        quantity=2,
        quality_gap=12.5,
    )
    second = RewindShapeRecommendation(
        RewindShape("shape_b", 3),
        1,
        3,
        1.0,
        quality_gap=4.0,
    )
    service = Service()
    dialog = _RewindRecommendationDialog(service, None)
    dialog._render_plans(RewindShapeAnalysis(None, "", 2, 8, (first, second)))

    assert len(dialog._editable_slots) == 8
    assert not dialog._save_plan_button.isEnabled()
    assert dialog._start_rewind_button.isEnabled()
    assert len(dialog.findChildren(QFrame, "rewindShapeRecommendationCard")) == 8
    assert sum(slot is not None for slot in dialog._editable_slots) == 3
    metric_labels = dialog.findChildren(QLabel, "rewindShapeMetrics")
    metrics = [label.text() for label in metric_labels]
    assert "缺分 12.5 · 库存 7 · 概率 25%" in metrics
    assert all("缺分" in text and "库存" in text and "概率" in text for text in metrics)
    assert all("方案数量" not in text for text in metrics)
    assert all("（" not in text and "）" not in text for text in metrics)
    assert all(not label.wordWrap() for label in metric_labels)


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


def test_rewind_shape_replacement_dialog_returns_the_selected_candidate() -> None:
    from PySide6.QtWidgets import QApplication

    from src.domain.rewind_shape_recommendation import RewindShape, RewindShapeRecommendation
    from src.features.toolbox.rewind_execution_dialog import RewindShapeReplacementDialog

    QApplication.instance() or QApplication([])
    first = RewindShapeRecommendation(RewindShape("shape_a", 2), 1, 0, 1.0)
    second = RewindShapeRecommendation(RewindShape("shape_b", 3), 1, 0, 1.0)
    dialog = RewindShapeReplacementDialog(None, candidates=(first, second), current_shape_id="shape_a")
    dialog._set_selected("shape_b")

    assert dialog.selected() == second


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


def test_rewind_dialog_cancel_uses_qdialog_result_without_instance_accepted(monkeypatch) -> None:
    from PySide6.QtWidgets import QDialog

    from src.features.toolbox import rewind_execution_ui
    from src.features.toolbox.rewind_execution_dialog import RewindExecutionOptions

    class CancelDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    class Host(rewind_execution_ui.RewindExecutionUiMixin):
        _rewind_options = RewindExecutionOptions()
        _saved_rewind_shape_ids = ()

        def _save_preferences(self):
            raise AssertionError("取消时不应保存偏好")

    monkeypatch.setattr(rewind_execution_ui, "RewindExecutionDialog", CancelDialog)
    Host()._configure_rewind()


def test_shape_picker_cancel_leaves_slots_unchanged_without_instance_accepted(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QDialog

    from src.features.toolbox import rewind_slot_ui
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def load_preferences(self):
            return {}

    class CancelDialog:
        def __init__(self, *_args, **_kwargs):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

    QApplication.instance() or QApplication([])
    dialog = _RewindRecommendationDialog(Service(), None)
    slots_before_cancel = tuple(dialog._editable_slots)
    monkeypatch.setattr(rewind_slot_ui, "RewindShapeReplacementDialog", CancelDialog)

    dialog._edit_rewind_slot(0)

    assert tuple(dialog._editable_slots) == slots_before_cancel


def test_shape_picker_groups_twelve_candidates_by_2_3_4_rows() -> None:
    from dataclasses import replace

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QLabel, QToolButton

    from src.features.toolbox.rewind_execution_dialog import RewindShapeReplacementDialog
    from src.features.toolbox.rewind_slot_ui import all_rewind_shape_candidates

    QApplication.instance() or QApplication([])
    candidates = list(all_rewind_shape_candidates())
    candidates[0] = replace(candidates[0], owned_count=7)
    dialog = RewindShapeReplacementDialog(None, candidates=tuple(candidates))
    assert dialog.minimumWidth() == 760
    assert dialog.maximumWidth() == 760
    row_labels = {label.text() for label in dialog.findChildren(QLabel)}
    assert {"2 型驱动", "3 型驱动", "4 型驱动"}.issubset(row_labels)
    assert len(dialog.findChildren(QToolButton, "rewindShapeReplacementOption")) == 12
    stock_labels = dialog.findChildren(QLabel, "rewindShapeReplacementStock")
    assert len(stock_labels) == 12
    assert next(
        label.text()
        for label in stock_labels
        if label.property("shapeId") == candidates[0].shape.shape_id
    ) == "库存 7"
    assert all(not label.wordWrap() for label in stock_labels)
    group_label = dialog.findChild(QLabel, "rewindShapeGroupLabel")
    assert group_label is not None
    assert group_label.alignment() & Qt.AlignLeft
    for option in dialog.findChildren(QToolButton, "rewindShapeReplacementOption"):
        assert option.text() == ""
        assert option.toolButtonStyle() == Qt.ToolButtonIconOnly


def test_shape_picker_maps_surfaces_and_text_for_each_theme() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QToolButton

    from src.app.theme import apply_app_theme
    from src.features.toolbox.rewind_execution_dialog import RewindShapeReplacementDialog
    from src.features.toolbox.rewind_slot_ui import all_rewind_shape_candidates

    app = QApplication.instance() or QApplication([])
    expected_colors = {
        "dark": ("background:#161b22", "color:#c9d1d9", "color:#8b949e"),
        "black": ("background:#080a0d", "color:#c9d1d9", "color:#8b949e"),
        "light": ("background:#f6f8fa", "color:#24292f", "color:#57606a"),
    }
    try:
        for theme, (surface, text, group_text) in expected_colors.items():
            apply_app_theme(app, theme)
            dialog = RewindShapeReplacementDialog(None, candidates=all_rewind_shape_candidates())
            option = dialog.findChild(QToolButton, "rewindShapeReplacementOption")
            group_label = dialog.findChild(QLabel, "rewindShapeGroupLabel")

            assert option is not None
            assert group_label is not None
            assert surface in option.styleSheet()
            assert text in option.styleSheet()
            assert group_text in group_label.styleSheet()
            dialog.deleteLater()
    finally:
        apply_app_theme(app, "dark")
