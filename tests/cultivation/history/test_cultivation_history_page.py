# 同一流程的公共行为回归合并，保留各自数据、失败与回滚断言。
from __future__ import annotations

# 验证养成历史整页接入、有效重算合并和加载失败保留原草稿。

import json
import os
import time
from dataclasses import replace
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NTE_TEST_TIER = "core"


def _wait(predicate):
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + 4.0
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)
    assert predicate()


def _planner():
    from src.domain.progression.stamina import IdentificationLevelProjection, ProgressionStaminaResult, StaminaPlanStatus
    from src.services.progression.character_requirements import MaterialSummaryStatus
    from src.services.progression.models import (
        CultivationForkSeed, CultivationMaterial, CultivationPlan, CultivationRole, CultivationSection,
        CultivationSeed, CultivationSkill, CultivationStaminaPlan,
        CultivationPreparedTarget,
    )

    class Planner:
        stamina_calls = 0
        material_calls = 0

        def list_roles(self):
            return (CultivationRole(101, "示例角色"),)

        def load_seed(self, _character_id):
            return CultivationSeed(101, "示例角色", 60, 4,
                                   (CultivationSkill("skill_a", "A", "普攻", 2, 10),),
                                   CultivationForkSeed("fork-a", "示例弧盘", 20, 0))

        def calculate(self, _request):
            self.material_calls += 1
            materials = (CultivationMaterial("material-a", "示例材料", 100),)
            return CultivationPlan("示例角色", MaterialSummaryStatus.COMPLETE,
                                   (CultivationSection("人物", materials),), materials, 0, 0, (), ())

        def prepare(self, request):
            return CultivationPreparedTarget(request, self.calculate(request), (), frozenset({"material-a"}))

        def calculate_stamina(self, _plan, **_kwargs):
            self.stamina_calls += 1
            total = ProgressionStaminaResult(StaminaPlanStatus.COMPLETE,
                                            IdentificationLevelProjection(60, 7, 7, False), (), (), 0, 0, (), ())
            return CultivationStaminaPlan(total, (), frozenset({"material-a"}))

        def load_farming_stages(self):
            return ()

        def dataset_metadata(self):
            return {"dataset_id": "fixture", "schema_version": 39, "importer_version": "1",
                    "built_at_utc": "2026-01-01T00:00:00Z"}

    return Planner()


def _page(tmp_path):
    from PySide6.QtWidgets import QApplication
    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.history_service import CultivationHistoryService
    from src.storage.sqlite.user_data_dao import UserDataDao

    application = QApplication.instance() or QApplication([])
    database = tmp_path / "user.sqlite3"
    with UserDataDao(database, account_id="one"):
        pass
    identity = lambda: ("one", 1, "fixture")
    history = CultivationHistoryService(account_id="one", user_database_path=database, context_identity=identity)
    planner = _planner()
    page = CultivationCalculatorPage(planner, context_identity=identity, history_service=history)
    return application, page, history, planner


def test_effective_recalculation_saves_one_configuration_result_pair(tmp_path):
    _application, page, history, _planner_service = _page(tmp_path)
    calculator = page.calculator
    calculator._calculate()
    _wait(lambda: history.list().total == 1)
    first = history.list().items[0]
    calculator.owned_materials.restore_history_materials([
        {"item_id": "material-a", "quantity": 30, "manual_override": True,
         "source": "manual", "observed_at_utc": None},
    ])
    calculator._calculate()
    _wait(lambda: history.list().items[0].revision == 2)
    assert history.list().total == 1
    saved = history.get(first.history_id)
    config = json.loads(saved.payload.configuration_json)
    result = json.loads(saved.payload.result_snapshot_json)
    assert config["owned_materials"][0]["quantity"] == 30
    assert result["materials"][0]["remaining"] == 70
    assert result["materials"][0]["allocated_equivalent"] == 30
    assert history.list().items[0].first_calculated_at_utc == first.first_calculated_at_utc
    page.shutdown()
    page.deleteLater()


def test_atomic_load_clones_config_without_solver_or_new_history(tmp_path):
    from src.services.progression.history_adapters import PreparedHistoryRestore
    from tests.cultivation.history.test_cultivation_history import history_payload

    _application, page, history, planner = _page(tmp_path)
    source = history_payload()
    configuration = json.loads(source.configuration_json)
    original = page.calculator
    seed = planner.load_seed(101)
    prepared = PreparedHistoryRestore("single", source.configuration_json, (seed,))
    page._replace_from_history(prepared)
    restored = page.calculator
    assert restored is not original
    draft = restored.export_history_configuration()
    assert draft["targets"][0]["skills_enabled"] is False
    assert draft["targets"][0]["fork"]["enabled"] is False
    assert "不参与计算" in restored._skills_toggle.toolTip()
    assert "不参与计算" in restored._fork_toggle.accessibleDescription()
    assert draft["targets"][0]["fork"]["target_level"] == 80
    assert draft["targets"][0]["skills"][0]["target_level"] == 8
    assert draft["owned_materials"] == configuration["owned_materials"]
    assert restored._last_plan is None
    assert planner.stamina_calls == 0
    assert history.list().total == 0
    page.shutdown()
    page.deleteLater()


def test_candidate_range_error_keeps_old_widget_and_all_inputs(tmp_path):
    from src.services.progression.history_adapters import PreparedHistoryRestore
    from tests.cultivation.history.test_cultivation_history import history_payload

    _application, page, history, planner = _page(tmp_path)
    original = page.calculator
    original._target_level.setValue(70)
    before = original.export_history_configuration()
    seed = planner.load_seed(101)
    narrowed = replace(seed, skills=(replace(seed.skills[0], maximum_level=3),))
    prepared = PreparedHistoryRestore("single", history_payload().configuration_json, (narrowed,))
    page._replace_from_history(prepared)
    assert page.calculator is original
    assert original.export_history_configuration() == before
    assert planner.stamina_calls == 0
    assert history.list().total == 0
    page.shutdown()
    page.deleteLater()


def test_late_restore_does_not_replace_new_account_draft(tmp_path):
    from src.services.progression.history_adapters import PreparedHistoryRestore
    from tests.cultivation.history.test_cultivation_history import history_payload

    _application, page, history, planner = _page(tmp_path)
    original = page.calculator
    original._target_level.setValue(70)
    before = original.export_history_configuration()
    page._initial_identity = ("different-account", 2, "fixture")
    prepared = PreparedHistoryRestore("single", history_payload().configuration_json, (planner.load_seed(101),))
    page._replace_from_history(prepared)
    assert page.calculator is original
    assert original.export_history_configuration() == before
    assert history.list().total == 0
    page.shutdown()
    page.deleteLater()


def test_repeated_history_load_closes_and_releases_old_bindings(tmp_path):
    from src.services.progression.history_adapters import PreparedHistoryRestore
    from tests.cultivation.history.test_cultivation_history import history_payload

    application, page, history, planner = _page(tmp_path)
    prepared = PreparedHistoryRestore("single", history_payload().configuration_json, (planner.load_seed(101),))
    previous = page.calculator._history_binding
    for _index in range(3):
        page._replace_from_history(prepared)
        assert previous._closed
        previous = page.calculator._history_binding
    assert len(history._bindings) == 2  # One live draft per mode, not one per historical load.
    page.shutdown()
    page.deleteLater()
    application.processEvents()

# 验证历史在三主题下的键盘选择、只读详情和默认取消，尺寸只断言可操作边界。

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

NTE_TEST_TIER = "core"


@pytest.mark.parametrize("theme", ["black"])
def test_keyboard_select_view_and_cancel_delete_in_three_themes(tmp_path, monkeypatch, theme):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QAbstractItemView, QLabel, QListView, QMessageBox
    from src.app.theme import apply_app_theme, current_theme_name
    from src.storage.sqlite.user_data_dao import UserDataDao
    from tests.cultivation.history.test_cultivation_history import history_payload
    from tests.cultivation.history.test_cultivation_history_page import _page
    from tests.cultivation.history.test_cultivation_history_page import _wait

    application, page, service, _planner_service = _page(tmp_path)
    previous_theme = current_theme_name()
    apply_app_theme(application, theme)
    try:
        with UserDataDao(tmp_path / "user.sqlite3") as dao:
            dao.save_cultivation_history("one", "keyboard-entry", history_payload())
        page.show()
        page._show_history()
        view = page._history_view
        _wait(lambda: view._table.topLevelItemCount() == 1)
        item = view._table.topLevelItem(0)
        view._table.setCurrentItem(item)
        view._table.setFocus()
        QTest.keyClick(view._table, Qt.Key.Key_Space)
        _wait(lambda: view._current_record is not None)
        assert len(view._selected) == 1
        assert view._load.isEnabled()
        assert any("合并已知体力" in label.text() for label in view.findChildren(QLabel))
        materials = view._detail.findChild(QListView, "cultivationHistoryMaterialTotals")
        assert materials.editTriggers() == QAbstractItemView.EditTrigger.NoEditTriggers
        assert not materials.model().flags(materials.model().index(0, 0)) & Qt.ItemFlag.ItemIsEditable
        assert view._delete.isEnabled()
        cancelled = []

        def cancel_with_keyboard(dialog):
            assert dialog.standardButton(dialog.defaultButton()) == QMessageBox.StandardButton.Cancel
            available = dialog.screen().availableGeometry()
            assert dialog.width() <= available.width() and dialog.height() <= available.height()
            dialog.show()
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            cancelled.append(True)
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "exec", cancel_with_keyboard)
        view._delete.setFocus()
        QTest.keyClick(view._delete, Qt.Key.Key_Space)
        assert cancelled == [True]
        assert service.list().total == 1
    finally:
        page.shutdown()
        page.close()
        page.deleteLater()
        application.processEvents()
        apply_app_theme(application, previous_theme)
