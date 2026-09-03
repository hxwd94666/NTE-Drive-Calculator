# 测试工具页倒带推荐、执行配置和候选驱动交互。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_rewind_role_picker_adds_highest_calculation_score_below_name() -> None:
    from PySide6.QtWidgets import QApplication, QLabel

    from src.app.theme import GRADE_COLORS
    from src.features.toolbox.page import _RoleSelectionDialog
    from src.services.rewind_shape_recommendation_service import RewindTargetRole

    QApplication.instance() or QApplication([])
    dialog = _RoleSelectionDialog(
        None,
        title="选择角色",
        description="测试",
        roles=(
            RewindTargetRole(1004, "安魂曲", None, calculation_score=251.25),
            RewindTargetRole(9001, "自建角色", None, is_custom=True),
        ),
        selected_character_ids=set(),
    )

    cards = {character_id: card for card, character_id, _name in dialog._cards}
    scored_label = cards[1004].findChild(QLabel, "rewindRoleCalculationScore")
    empty_label = cards[9001].findChild(QLabel, "rewindRoleCalculationScore")
    assert cards[1004].text() == "安魂曲"
    assert scored_label.text() == "最高分 251.25 · SS"
    assert GRADE_COLORS["SS"] in scored_label.styleSheet()
    assert cards[1004].property("rewindCalculationScore") == 251.25
    assert cards[9001].text() == "自建角色"
    assert empty_label.text() == ""
    assert cards[9001].property("rewindCalculationScore") is None
    assert cards[1004].height() == cards[9001].height() == 132


def test_cultivation_calculator_prefills_role_state_and_renders_merged_totals() -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from src.services.character_progression_requirements import (
        MaterialSummaryStatus,
    )
    from src.features.toolbox.cultivation_calculator import CultivationCalculatorDialog
    from src.services.cultivation_planner_service import (
        CultivationMaterial,
        CultivationPlan,
        CultivationRole,
        CultivationSection,
        CultivationForkSeed,
        CultivationSeed,
        CultivationSkill,
    )

    class Service:
        def __init__(self):
            self.requests = []

        def list_roles(self):
            return (CultivationRole(1004, "安魂曲"),)

        def load_seed(self, _character_id):
            return CultivationSeed(
                1004, "安魂曲", 20, 1,
                (CultivationSkill("skill-a", "Q", "终结技", 3, 10),),
                CultivationForkSeed("fork-a", "专属弧盘", 40, 2),
            )

        def calculate(self, request):
            self.requests.append(request)
            assert request.current_level == 20
            assert request.current_breakthrough_stage == 1
            assert request.target_level == 80
            if request.include_character_progression:
                assert request.fork is not None
                assert request.fork.fork_id == "fork-a"
                assert request.fork.current_level == 40
            else:
                assert not request.include_skills
                assert request.fork is None
            return CultivationPlan(
                "安魂曲", MaterialSummaryStatus.COMPLETE,
                (CultivationSection("Q · 终结技", (CultivationMaterial("m-1", "材料甲", 4),)),),
                (CultivationMaterial("m-1", "材料甲", 4),), 100, 0, (2,), (), 300,
            )

    QApplication.instance() or QApplication([])
    service = Service()
    dialog = CultivationCalculatorDialog(service, None)
    QApplication.processEvents()

    assert dialog._current_level.value() == 20
    assert dialog._current_stage.currentData() == 1
    assert dialog._target_level.value() == 80
    assert dialog._skill_inputs["skill-a"][0].value() == 3
    assert dialog._fork.text() == "专属弧盘"
    assert dialog._fork_current_level.value() == 40
    assert dialog._fork_current_level.isEnabled()
    assert dialog._result.parentWidget().objectName() == "cultivationCalculatorResultPanel"
    assert not any(label.text() == "养成计算器" for label in dialog.findChildren(QLabel))
    assert dialog.findChild(QPushButton, "cultivationCalculatorCalculate").text() == "计算所需材料"
    assert dialog.findChild(QPushButton, "cultivationCalculatorCalculate").minimumHeight() == 44
    dialog.resize(1120, 740)
    dialog.show()
    QApplication.processEvents()
    skill_x = dialog._skills_toggle.mapTo(dialog, QPoint()).x()
    fork_x = dialog._fork_toggle.mapTo(dialog, QPoint()).x()
    assert skill_x <= fork_x

    dialog._calculate()
    rendered = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    assert "材料数据完整" in rendered
    assert "材料甲 × 4" in rendered
    assert "Q · 终结技" in rendered
    assert dialog._copy_button.isEnabled()
    assert service.requests[-1].include_character_progression
    assert service.requests[-1].include_skills

    dialog._character_toggle.setChecked(False)
    dialog._fork_toggle.setChecked(False)
    dialog._skills_toggle.setChecked(False)
    assert not dialog._current_level.isEnabled()
    assert not dialog._fork_current_level.isEnabled()
    assert not dialog._skill_inputs["skill-a"][0].isEnabled()
    dialog._calculate()
    assert not service.requests[-1].include_character_progression
    assert not service.requests[-1].include_skills
    assert service.requests[-1].fork is None


def test_cultivation_image_selector_is_a_searchable_single_choice_card_grid() -> None:
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_selectors import CultivationImageSelector

    QApplication.instance() or QApplication([])
    dialog = CultivationImageSelector(
        None,
        title="选择弧盘",
        description="测试",
        options=(("fork-a", "赤红弧盘", None), ("fork-b", "青蓝弧盘", None)),
        selected_id="fork-a",
    )

    cards = {option_id: card for card, option_id, _name in dialog._cards}
    assert dialog.selected_id() == "fork-a"
    assert cards["fork-a"].size().width() == 116
    assert cards["fork-a"].size().height() == 132
    cards["fork-b"].setChecked(True)
    assert dialog.selected_id() == "fork-b"
    dialog._search.setText("qinglan")
    QApplication.processEvents()
    assert cards["fork-a"].isHidden()
    assert not cards["fork-b"].isHidden()


def test_cultivation_spinboxes_draw_theme_contrast_step_chevrons() -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox

    from src.app.theme import theme_color
    from src.features.toolbox.cultivation_calculator import _CultivationSpinBox

    app = QApplication.instance() or QApplication([])
    previous = app.property("nte_effective_theme")
    try:
        for theme in ("light", "black"):
            app.setProperty("nte_effective_theme", theme)
            spinbox = _CultivationSpinBox()
            spinbox.resize(100, 42)
            spinbox.show()
            QApplication.processEvents()
            option = QStyleOptionSpinBox()
            spinbox.initStyleOption(option)
            rect = spinbox.style().subControlRect(
                QStyle.ComplexControl.CC_SpinBox,
                option,
                QStyle.SubControl.SC_SpinBoxUp,
                spinbox,
            )
            color = spinbox.grab().toImage().pixelColor(
                rect.center().x(), rect.center().y() - 2,
            )
            expected = QColor(theme_color("#8b949e"))
            assert abs(color.red() - expected.red()) < 60
            assert abs(color.green() - expected.green()) < 60
            assert abs(color.blue() - expected.blue()) < 60
            spinbox.close()
    finally:
        app.setProperty("nte_effective_theme", previous)


def test_official_replacement_summary_explains_score_and_third_percentage() -> None:
    from src.ui.controllers.official_role_replacement_controller import (
        OFFICIAL_ROLE_REPLACEMENT_SUMMARY,
    )

    assert OFFICIAL_ROLE_REPLACEMENT_SUMMARY == (
        "评分按该角色的直伤权重计算，候选由高到低排列；"
        "卡片第三项百分比表示该装备带来的直伤收益。"
    )


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
    assert service.saved["target_threshold_mode"] == "grade"
    assert service.saved["target_custom_percent"] is None
    assert service.saved["rewind_qualities"] == ["purple", "gold"]
    assert service.saved["rewind_drive_customization"] == "enabled"
    reopened = _RewindRecommendationDialog(service, None)
    assert reopened._rewind_options == RewindExecutionOptions(
        qualities=("purple", "gold"),
        drive_customization="enabled",
    )


def test_rewind_custom_percentage_persists_and_is_passed_to_analysis(monkeypatch) -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox import page as toolbox_page
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def __init__(self) -> None:
            self.saved = {}
            self.request = None

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

        def analyze_for_targets(self, **kwargs):
            self.request = kwargs
            return object()

    class ImmediateWorker:
        def __init__(self, *, target, parent=None) -> None:
            self.target = target
            self.parent = parent
            self.result_ready = type("Signal", (), {"connect": lambda *_args: None})()
            self.error = type("Signal", (), {"connect": lambda *_args: None})()
            self.finished = type("Signal", (), {"connect": lambda *_args: None})()

        def start(self) -> None:
            self.target()

        def deleteLater(self) -> None:
            pass

    QApplication.instance() or QApplication([])
    service = Service()
    dialog = _RewindRecommendationDialog(service, None)
    assert dialog._custom_percent_input.text() == ""
    assert dialog._custom_percent_input.width() == 60
    grade_help = next(
        button
        for button in dialog.findChildren(QPushButton, "btnHelp")
        if "评分等级" in button.toolTip()
    )
    shown: dict[str, str] = {}
    def capture_message(message):
        shown.update(
            title=message.windowTitle(),
            detail=message.text(),
            icon=message.icon(),
        )
        return 0

    monkeypatch.setattr(toolbox_page.QMessageBox, "exec", capture_message)
    grade_help.click()
    assert shown["title"] == "自选评分等级说明"
    assert shown["icon"] == toolbox_page.QMessageBox.Icon.NoIcon
    assert shown["detail"].splitlines() == [
        "D：0%", "C：20%", "B：30%", "A：40%", "S：50%", "SS：60%", "SSS：70%", "ACE：80%",
        "自选：以填写百分比为准",
    ]
    dialog._target_character_ids = {1004}
    dialog._set_custom_target()
    dialog._custom_percent_input.setValue(90.0)

    assert service.saved["target_threshold_mode"] == "custom"
    assert service.saved["target_custom_percent"] == 90.0
    assert dialog._custom_percent_input.isEnabled()

    reopened = _RewindRecommendationDialog(service, None)
    assert reopened._target_threshold_mode == "custom"
    assert reopened._custom_percent_input.value() == 90.0
    assert reopened._custom_percent_input.isEnabled()

    monkeypatch.setattr(toolbox_page, "WorkerThread", ImmediateWorker)
    dialog._refresh_analysis()

    assert service.request is not None
    assert service.request["target_grade"] == "S"
    assert service.request["target_custom_percent"] == 90.0


def test_rewind_custom_percentage_spin_buttons_have_theme_contrast() -> None:
    from src.app.theme import DARK_STYLE

    assert "QDoubleSpinBox#rewindCustomPercent:enabled" in DARK_STYLE
    assert "QToolButton#rewindPercentStepUp" in DARK_STYLE
    assert "background:#1f6feb33" in DARK_STYLE
    assert "border:1px solid #58a6ff" in DARK_STYLE
    assert "color:#58a6ff" in DARK_STYLE
    assert "min-width:20px" in DARK_STYLE


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


def test_rewind_execution_registers_and_releases_the_global_stop_hotkey(monkeypatch) -> None:
    from types import SimpleNamespace

    from PySide6.QtWidgets import QApplication, QWidget

    from src.features.toolbox import rewind_execution_ui
    from src.features.toolbox.page import _RewindRecommendationDialog
    from src.features.toolbox.rewind_execution_dialog import RewindExecutionDialog

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

        def start(self) -> None:
            pass

        def deleteLater(self) -> None:
            pass

    class FakeHotkeys:
        def __init__(self) -> None:
            self.configuration = SimpleNamespace(stop="F8")
            self.active_owner = None
            self.on_stop = None

        def start(self, *, owner, on_stop) -> None:
            self.active_owner = owner
            self.on_stop = on_stop

        def stop(self, *, owner) -> None:
            if self.active_owner == owner:
                self.active_owner = None

    class Service:
        def load_preferences(self):
            return {}

    QApplication.instance() or QApplication([])
    host = QWidget()
    hotkeys = FakeHotkeys()
    host.global_hotkey_manager = hotkeys
    dialog = _RewindRecommendationDialog(Service(), host)
    execution_dialog = RewindExecutionDialog(dialog)
    assert execution_dialog._stop_hotkey_label() == "F8"
    dialog._rewind_foreground_settle_seconds = 0
    captured = {}
    monkeypatch.setattr(rewind_execution_ui, "WorkerThread", FakeWorker)
    monkeypatch.setattr(dialog, "_prepare_rewind_game_foreground", lambda: None)
    monkeypatch.setattr(
        rewind_execution_ui,
        "execute_rewind_request",
        lambda _request, *, should_stop: captured.setdefault("stopped", should_stop()),
    )

    dialog._start_rewind_execution()

    worker = dialog._rewind_worker
    assert isinstance(worker, FakeWorker)
    assert hotkeys.active_owner == "rewind_execution"
    assert hotkeys.on_stop is not None
    hotkeys.on_stop()
    assert worker.target() is True
    worker.finished.emit()
    assert hotkeys.active_owner is None


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
