# 测试工具页倒带推荐、执行配置和候选驱动交互。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")



def test_cultivation_calculator_prefills_role_state_and_renders_merged_totals() -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QPushButton,
        QToolButton,
        QWidget,
    )

    from src.services.progression.character_requirements import (
        MaterialSummaryStatus,
    )
    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.ui.progression_material_card import ProgressionMaterialCard
    from src.services.progression.planner_service import (
        CultivationMaterial,
        CultivationPlan,
        CultivationRole,
        CultivationSection,
        CultivationForkSeed,
        CultivationSeed,
        CultivationSkill,
    )

    from tests.cultivation_planner_fixture import PlanningFixture
    from tests.test_cultivation_history_page import _wait

    class Service(PlanningFixture):
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
            materials = (
                CultivationMaterial(
                    "WeaponUpMaterial_lv3", "混沌染剂", 364, quality=5,
                ),
                CultivationMaterial(
                    "WeaponUpMaterial_lv1", "淡色染剂", 1,
                ),
                CultivationMaterial("Fons", "方斯", 1_344_150),
                CultivationMaterial(
                    "WeaponBreakMaterial_02_lv3", "弧盘突破材料", 12,
                ),
            )
            return CultivationPlan(
                "安魂曲", MaterialSummaryStatus.COMPLETE,
                (CultivationSection(
                    "弧盘升级",
                    materials,
                ), CultivationSection("弧盘突破", materials[:3])),
                materials,
                100, 0, (2,), (), 3_640_330, 170, (1, 2, 3, 4, 5, 6),
            )

    QApplication.instance() or QApplication([])
    service = Service()
    page = CultivationCalculatorPage(service, None)
    # 本例核对全部材料合计，显式选择范围，不把默认体力筛选当作完整材料视图。
    page.material_scope.setCurrentIndex(page.material_scope.findData("all"))
    dialog = page.calculator
    QApplication.processEvents()

    assert dialog._current_level.value() == 20
    assert dialog._current_stage.currentData() == 1
    assert dialog._target_level.value() == 80
    assert dialog._skill_inputs["skill-a"][0].value() == 3
    # 弧盘身份投影在名称卡；更换按钮表示动作，不承载已选弧盘名。
    assert any(label.text() == "专属弧盘" for label in dialog.findChildren(QLabel))
    assert dialog._fork_current_level.value() == 40
    assert dialog._fork_current_level.isEnabled()
    page.resize(1120, 740)
    page.show()
    QApplication.processEvents()

    dialog._calculate()
    # 正式计算经后台 owner 交付，以公共复制资格确认当前结果已完成，不假定一次事件处理即可。
    _wait(lambda: page.copy_button.isEnabled())
    rendered = "\n".join(label.text() for label in dialog.findChildren(QLabel))
    material_cards = dialog.findChildren(ProgressionMaterialCard)
    assert material_cards
    icon = material_cards[0].findChild(QLabel, "progressionMaterialIcon")
    assert icon is not None and not icon.pixmap().isNull()
    assert "混沌染剂" in rendered
    assert "× 364" in rendered
    assert "弧盘升级" in rendered
    assert "弧盘升级经验 3,640,330，材料最小溢出 170" in rendered
    totals = dialog.findChild(QFrame, "cultivationCalculatorTotals")
    details = dialog.findChild(QFrame, "cultivationCalculatorDetailsPanel")
    total_cards = totals.findChildren(ProgressionMaterialCard)
    details_toggle = details.findChild(
        QToolButton, "cultivationCalculatorDetailsToggle"
    )
    details_content = details.findChild(
        QWidget, "cultivationCalculatorDetailsContent"
    )
    assert len(total_cards) == 4
    assert page.scroll.horizontalScrollBar().maximum() == 0
    assert not details_toggle.isChecked()
    assert not details_content.isVisibleTo(dialog)
    details_toggle.setFocus()
    QTest.keyClick(details_toggle, Qt.Key.Key_Space)
    QApplication.processEvents()
    assert details_toggle.isChecked()
    assert details_content.isVisibleTo(dialog)
    _wait(lambda: page.scroll.verticalScrollBar().maximum() > 0)
    assert page.scroll.verticalScrollBar().maximum() > 0
    detail_cards = [
        card for card in details_content.findChildren(QFrame)
        if card.objectName() == "cultivationCalculatorResultSection"
    ]
    assert len(detail_cards) == 2
    assert page.copy_button.isEnabled()
    assert service.requests[-1].include_character_progression
    assert service.requests[-1].include_skills

    dialog._character_toggle.setChecked(False)
    dialog._fork_toggle.setChecked(False)
    dialog._skills_toggle.setChecked(False)
    assert not dialog._current_level.isEnabled()
    assert not dialog._fork_current_level.isEnabled()
    assert not dialog._skill_inputs["skill-a"][0].isEnabled()
    dialog._calculate()
    _wait(lambda: page.copy_button.isEnabled())
    assert dialog.findChild(
        QToolButton, "cultivationCalculatorDetailsToggle"
    ).isChecked()
    assert not service.requests[-1].include_character_progression
    assert not service.requests[-1].include_skills
    assert service.requests[-1].fork is None
    page.findChild(QPushButton, "cultivationCalculatorReset").click()
    QApplication.processEvents()
    assert not page.copy_button.isEnabled()
    assert dialog._current_level.value() == 20
    page.shutdown()
    _wait(lambda: not dialog._controller.is_running() and not page.batch_calculator._controller.is_running())
    page.close()
    page.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_progression_material_grid_reflows_without_horizontal_rows() -> None:
    from PySide6.QtWidgets import QApplication

    from src.ui.progression_material_card import ProgressionMaterialCard
    from src.ui.progression_material_grid import ProgressionMaterialGrid

    QApplication.instance() or QApplication([])
    grid = ProgressionMaterialGrid()
    cards = tuple(
        ProgressionMaterialCard(
            name=f"材料 {index}",
            amount_text=f"× {index}",
            compact=True,
            parent=grid,
        )
        for index in range(1, 6)
    )
    grid.set_cards(cards)
    grid.resize(700, 320)
    grid.show()
    QApplication.processEvents()

    assert all(grid.rect().contains(card.geometry()) for card in cards)

    grid.resize(260, 640)
    QApplication.processEvents()

    assert all(grid.rect().contains(card.geometry()) for card in cards)
    grid.close()


def test_rewind_execution_dialog_accept_persists_and_reopens_account_options(
    monkeypatch,
) -> None:
    from PySide6.QtWidgets import QApplication, QDialog

    from src.features.toolbox.rewind import execution_ui as rewind_execution_ui
    from src.features.toolbox.page import _RewindRecommendationDialog
    from src.features.toolbox.rewind.execution_dialog import RewindExecutionOptions

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
    dialog = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
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
    reopened = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
    assert reopened._rewind_options == RewindExecutionOptions(
        qualities=("purple", "gold"),
        drive_customization="enabled",
    )


def test_rewind_custom_percentage_persists_and_is_passed_to_analysis(monkeypatch) -> None:
    from PySide6.QtCore import QCoreApplication, QEvent, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

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

    app = QApplication.instance() or QApplication([])
    service = Service()
    # 本例只验证百分比编辑与请求冻结，角色目录另有异步生命周期用例。
    monkeypatch.setattr(_RewindRecommendationDialog, "_load_roles_async", lambda _self: None)
    dialog = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
    owners = [dialog]
    try:
        assert dialog._custom_percent_input.text() == ""
        dialog._target_character_ids = {1004}
        dialog._set_custom_target()
        dialog.show()
        app.processEvents()
        editor = dialog._custom_percent_input.lineEdit()
        editor.setFocus()
        QTest.keyClicks(editor, "90.5")
        QTest.keyClick(editor, Qt.Key_Tab)

        assert service.saved["target_threshold_mode"] == "custom"
        assert service.saved["target_custom_percent"] == 90.5
        assert dialog._custom_percent_input.isEnabled()
        QTest.mouseDClick(editor, Qt.LeftButton, pos=editor.rect().center())
        assert editor.selectedText().removesuffix("%") == "90.5"
        QTest.keyClicks(editor, "91.7")
        QTest.keyClick(editor, Qt.Key_Tab)
        assert dialog._custom_percent_input.value() == 91.7
        assert service.saved["target_custom_percent"] == 91.7

        reopened = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
        owners.append(reopened)
        assert reopened._target_threshold_mode == "custom"
        assert reopened._custom_percent_input.value() == 91.7
        assert reopened._custom_percent_input.isEnabled()

        from src.features.toolbox.rewind import selection_ui as rewind_selection_ui
        monkeypatch.setattr(rewind_selection_ui, "WorkerThread", ImmediateWorker)
        dialog._refresh_analysis()

        assert service.request is not None
        assert service.request["target_grade"] == "S"
        assert service.request["target_custom_percent"] == 91.7
        from src.domain.rewind_shape_recommendation import target_percentage_score
        assert abs(target_percentage_score(service.request["target_custom_percent"], 3) - 27.51) < 1e-10
        assert dialog._custom_percent_input.minimum() == 0.0
        assert dialog._custom_percent_input.maximum() == 100.0
        for text, value in (("100.0", 100.0), ("0.0", None)):
            editor.setFocus()
            dialog._custom_percent_input.selectAll()
            QTest.keyClicks(editor, text)
            QTest.keyClick(editor, Qt.Key_Tab)
            assert service.saved["target_custom_percent"] == value
    finally:
        # 只回收本例创建的对话框，键入行为不接触真实游戏输入。
        for owner in owners:
            owner.close()
            owner.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_rewind_execution_dialog_shows_stop_key_and_disables_custom_for_blue_only() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from src.features.toolbox.rewind.execution_dialog import (
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

    assert "执行期间可按设置中的全局停止键F12停止。" in descriptions
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

    from src.features.drive_assembly.rewind.execution import RewindExecutionReport
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def load_preferences(self):
            return {}

    QApplication.instance() or QApplication([])
    host = QWidget()
    host.show()
    dialog = _RewindRecommendationDialog(Service(), host, operation_entry=lambda *_: True)
    dialog.show()

    dialog._prepare_rewind_game_foreground()

    assert dialog.isHidden()
    assert host.isMinimized()

    dialog._on_rewind_complete(RewindExecutionReport(1850, 30, 3, 0, 5, "difficulty_low", (), 50))

    assert not dialog.isHidden()
    assert not host.isMinimized()
    assert dialog._start_rewind_button.text() == "输入已结束：计划 30 次，预计剩余 50"

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

    assert dialog._start_rewind_button.text() == "输入已结束：计划 50 次，预计金剩50、蓝剩20"


def test_saved_rewind_plan_restores_quality_gap_without_another_analysis() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from copy import deepcopy

    from src.domain.rewind_shape_recommendation import RewindShape, RewindShapeRecommendation
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def __init__(self) -> None:
            self.saved: dict[str, object] = {"saved_rewind_shape_ids": ["EquipmentGeometry_Hen2"] * 8}

        def load_preferences(self):
            return dict(self.saved)

        def save_preferences(self, value):
            self.saved = dict(value)

    QApplication.instance() or QApplication([])
    service = Service()
    dialog = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
    # 旧的 ID-only 保存格式仍能恢复，不依赖首次打开自动求解。
    assert dialog._slots_complete()
    assert all(slot.shape.shape_id == "EquipmentGeometry_Hen2" for slot in dialog._editable_slots)
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

    reopened = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
    assert reopened._slots_complete()
    assert [slot.quality_gap for slot in reopened._editable_slots if slot is not None] == [
        12.5
    ] * 8
    assert any("12.5" in label.text() for label in reopened.findChildren(QLabel, "rewindShapeMetrics"))
    saved_preferences = deepcopy(service.saved)
    saved_slots = tuple(reopened._editable_slots)
    reopened.findChild(QPushButton, "rewindClearCandidates").click()
    assert not any(reopened._editable_slots)
    assert service.saved == saved_preferences
    again = _RewindRecommendationDialog(service, None, operation_entry=lambda *_: True)
    assert tuple(again._editable_slots) == saved_slots


def test_generating_another_strategy_replaces_the_current_transient_slots() -> None:
    from PySide6.QtWidgets import QApplication

    from src.domain.rewind_shape_recommendation import RewindShape, RewindShapeRecommendation
    from src.features.toolbox.page import _RewindRecommendationDialog

    class Service:
        def load_preferences(self):
            return {}

    QApplication.instance() or QApplication([])
    dialog = _RewindRecommendationDialog(Service(), None, operation_entry=lambda *_: True)
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


def test_rewind_quality_gap_ceils_to_hundredths_on_render_and_inventory_refresh() -> None:
    from PySide6.QtWidgets import QApplication, QLabel

    from src.features.toolbox.page import _RewindRecommendationDialog

    gaps = (3.55271e-15, 0.0, 12.5, 12.344, 12.346, 1_000_000.0, 0.004, 0.006)
    expected = ("0.01", "0.00", "12.50", "12.35", "12.35", "1000000.00", "0.01", "0.01")

    class Service:
        def load_preferences(self):
            return {
                "saved_rewind_slots": [
                    {"shape_id": "EquipmentGeometry_Hen2", "quality_gap": gap}
                    for gap in gaps
                ],
            }

    QApplication.instance() or QApplication([])
    dialog = _RewindRecommendationDialog(Service(), None, operation_entry=lambda *_: True)
    try:
        labels = dialog.findChildren(QLabel, "rewindShapeMetrics")
        assert len(labels) == len(gaps)
        for label in labels:
            index = int(label.property("slotIndex"))
            assert label.text().split(" · ")[0] == f"缺分 {expected[index]}"

        dialog._set_replacement_inventory_counts({"EquipmentGeometry_Hen2": 77})
        for label in labels:
            index = int(label.property("slotIndex"))
            assert label.text().split(" · ")[:2] == [f"缺分 {expected[index]}", "库存 77"]
        assert [slot["quality_gap"] for slot in dialog._serialize_rewind_slots()] == list(gaps)
    finally:
        dialog.close()


def test_rewind_quality_gap_ceiling_preserves_exact_hundredths() -> None:
    from math import inf, nextafter

    from src.features.toolbox.rewind.slot_ui import _format_quality_gap

    assert _format_quality_gap(0.01) == "0.01"
    assert _format_quality_gap(0.07) == "0.07"
    # 原维护用例的这个字面量与 0.07 是同一 float；用下一可表示值覆盖真正的越界。
    assert 0.07000000000000001 == 0.07
    assert _format_quality_gap(nextafter(0.07, inf)) == "0.08"
    assert _format_quality_gap(12.341) == "12.35"
    assert _format_quality_gap(0.0001) == "0.01"
