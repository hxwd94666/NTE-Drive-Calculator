# 测试养成计算器在工具页内的整页导航与账号草稿生命周期。
from __future__ import annotations

import os
from dataclasses import replace
from tests.cultivation_planner_fixture import PlanningFixture

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

def test_cultivation_uses_one_verified_role_catalog_for_data_and_images(tmp_path) -> None:
    from src.app.context import ApplicationPaths
    from src.integrations.role_catalog_release import RoleCatalogRelease

    paths = ApplicationPaths.from_roots(
        root=tmp_path, app_dir=tmp_path, data_root=tmp_path,
        bundled_config_dir=tmp_path / "config",
        asset_dir=tmp_path / "assets", app_icon_path=tmp_path / "assets" / "app_icon.ico",
    )
    assert paths.cultivation_database_path == paths.static_database_path
    assert paths.cultivation_asset_root == tmp_path / "data" / "role_catalog" / "game_ui"

    release = RoleCatalogRelease(
        database_path=tmp_path / "role_catalog" / "game_static.sqlite3",
        asset_root=tmp_path / "role_catalog" / "game_ui",
        dataset_id="reference-dataset",
        sha256="fixture-digest",
        scope="reference",
    )
    with_catalog = replace(paths, role_catalog=release)
    assert with_catalog.cultivation_database_path == release.database_path
    assert with_catalog.cultivation_asset_root == release.asset_root
    assert with_catalog.static_database_path == paths.static_database_path
    assert with_catalog.game_ui_asset_root == release.asset_root
    assert with_catalog.equipment_allocation_database_path == paths.static_database_path
    assert with_catalog.equipment_allocation_asset_root == release.asset_root
    role_only = replace(with_catalog, role_catalog=replace(release, scope="role_page"))
    assert role_only.equipment_allocation_database_path == paths.static_database_path
    assert role_only.equipment_allocation_asset_root == release.asset_root


def _dispose_widget(widget) -> None:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    widget.close()
    widget.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_cultivation_context_identity_ignores_database_access_time(tmp_path) -> None:
    from src.features.toolbox.toolbox_navigation import (
        cultivation_context_identity,
    )

    database = tmp_path / "game_static.sqlite3"
    database.write_bytes(b"static")
    before = cultivation_context_identity("account", 3, database)
    stat = database.stat()
    os.utime(
        database,
        ns=(stat.st_atime_ns + 1_000_000_000, stat.st_mtime_ns),
    )

    assert cultivation_context_identity("account", 3, database) == before
    database.write_bytes(b"replaced-static")
    assert cultivation_context_identity("account", 3, database) != before


def test_toolbox_opens_cultivation_as_page_and_preserves_same_session_draft() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QStackedWidget, QWidget

    from src.features.toolbox.page import ToolboxDependencies, ToolboxPage

    class Service(PlanningFixture):
        def list_roles(self):
            return ()

    QApplication.instance() or QApplication([])
    identity = {"value": ("account-a", 0, "dataset-a")}
    services: list[Service] = []

    def build_service() -> Service:
        service = Service()
        services.append(service)
        return service

    owner = QWidget()
    toolbox = ToolboxPage(
        dependencies=ToolboxDependencies(
            rewind_service_factory=lambda: object(),
            cultivation_service_factory=build_service,
            navigate_static_catalog=lambda: None,
            cultivation_context_identity=lambda: identity["value"],
        ),
        dialog_parent=owner,
    )
    root = toolbox.build()
    stack = root.findChild(QStackedWidget, "toolboxPageStack")
    entry = root.findChild(QPushButton, "toolboxCultivationCalculator")

    assert stack.currentWidget().objectName() == "toolboxHomePage"
    assert toolbox._cultivation_navigation.page is None
    entry.click()
    QApplication.processEvents()

    first_page = toolbox._cultivation_navigation.page
    assert first_page is not None
    assert stack.currentWidget() is first_page
    assert len(services) == 1
    assert not first_page.isWindow()
    first_page.calculator._current_level.setValue(42)

    root.findChild(QPushButton, "cultivationCalculatorBack").click()
    assert stack.currentWidget().objectName() == "toolboxHomePage"
    entry.click()
    assert toolbox._cultivation_navigation.page is first_page
    assert first_page.calculator._current_level.value() == 42
    assert len(services) == 1

    identity["value"] = ("account-b", 1, "dataset-a")
    toolbox.refresh()
    QApplication.processEvents()
    assert toolbox._cultivation_navigation.page is None
    assert stack.currentWidget().objectName() == "toolboxHomePage"

    entry.click()
    QApplication.processEvents()
    assert toolbox._cultivation_navigation.page is not first_page
    assert len(services) == 2
    _dispose_widget(root)
    _dispose_widget(owner)


def test_cultivation_owned_materials_deduct_and_preserve_scope_edits() -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import QEvent, QPoint, QRect
    from PySide6.QtTest import QSignalSpy
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QPushButton,
        QSpinBox,
    )

    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.character_requirements import MaterialSummaryStatus
    from src.services.progression.planner_service import (
        CultivationMaterial,
        CultivationPlan,
        CultivationRole,
        CultivationSection,
        CultivationSeed,
    )
    from tests.test_cultivation_history_page import _wait

    materials = tuple(
        CultivationMaterial(f"item-{index}", f"材料 {index}", index * 10)
        for index in range(1, 6)
    )

    class Service(PlanningFixture):
        stamina_calls = 0

        def list_roles(self):
            return (CultivationRole(1004, "测试角色"),)

        def load_seed(self, _character_id):
            return CultivationSeed(1004, "测试角色", 1, 0, (), None)

        def calculate(self, _request):
            return CultivationPlan(
                "测试角色",
                MaterialSummaryStatus.COMPLETE,
                (CultivationSection("角色升级", materials),),
                materials,
                0,
                0,
                (),
                (),
            )

        def calculate_stamina(self, _plan, **_kwargs):
            self.stamina_calls += 1
            result = SimpleNamespace(
                total_stamina=120,
                known_stamina=120,
                runs=(),
                unresolved_item_ids=(),
            )
            return SimpleNamespace(
                total=result,
                sections=(SimpleNamespace(result=result),),
                stamina_item_ids=frozenset({"item-1", "item-3"}),
            )

    QApplication.instance() or QApplication([])
    service = Service()
    page = CultivationCalculatorPage(service)
    assert page.material_scope.currentData() == "stamina"
    page.material_scope.setCurrentIndex(0)
    page.resize(1180, 760)
    page.show()
    completed = QSignalSpy(page.calculator.calculation_completed)
    page.calculator._calculate()
    # 以已交付次数等待，兼顾线程和 Qt 事件泵，不依赖进入嵌套等待时信号刚好尚未到达。
    _wait(lambda: completed.count() == 1)
    assert service.stamina_calls == 1
    sync = page.findChild(QPushButton, "cultivationOwnedImport")
    assert sync is not None and not sync.isEnabled()
    owned_materials = page.calculator._owned_materials
    owned_input = owned_materials.findChild(QSpinBox, "cultivationOwnedMaterialQuantity")
    assert owned_input is not None
    assert owned_materials.select_material("item-1")
    owned_input.setValue(3)
    assert owned_materials.quantities()["item-1"] == 3
    assert service.stamina_calls == 1
    assert not page.copy_button.isEnabled()
    refreshed = tuple(
        CultivationMaterial(
            material.item_id,
            material.name,
            material.quantity + 1,
        )
        for material in materials
    )
    owned_materials.set_materials(refreshed)
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert owned_materials.findChild(QSpinBox, "cultivationOwnedMaterialQuantity") is owned_input
    assert owned_input.value() == 3
    assert owned_materials.required_quantity("item-1") == 11
    page.calculator._calculate_button.click()
    _wait(lambda: completed.count() == 2)
    QApplication.processEvents()
    assert service.stamina_calls == 2
    # 输入编辑器须完整进入外层视口，不把某个固定像素偏移当作可操作性。
    _wait(lambda: page.scroll.viewport().rect().contains(QRect(
        owned_input.mapTo(page.scroll.viewport(), QPoint()), owned_input.size())))
    assert page.copy_button.isEnabled()

    totals = next(
        frame
        for frame in page.findChildren(QFrame, "cultivationCalculatorTotals")
        if not frame.isHidden()
    )
    assert {label.text() for label in totals.findChildren(QLabel, "progressionMaterialCardName")} == {item.name for item in materials}
    rendered = [label.text() for label in totals.findChildren(QLabel)]
    assert "× 7" in rendered
    assert "最低体力 120" in rendered
    page.material_scope.setCurrentIndex(1)
    QApplication.processEvents()
    assert service.stamina_calls == 2
    assert owned_materials.required_quantity("item-1") == 10
    assert owned_materials.required_quantity("item-2") is None
    scoped_totals = next(
        frame for frame in page.findChildren(QFrame, "cultivationCalculatorTotals")
        if not frame.isHidden()
    )
    assert {label.text() for label in scoped_totals.findChildren(QLabel, "progressionMaterialCardName")} == {materials[0].name, materials[2].name}
    page.calculator.copy_plan()
    assert "材料 1" in QApplication.clipboard().text()
    assert "材料 2" not in QApplication.clipboard().text()
    page.material_scope.setCurrentIndex(0)
    assert owned_materials.select_material("item-2")
    owned_input.setValue(4)
    assert not page.copy_button.isEnabled()
    page.material_scope.setCurrentIndex(1)
    assert owned_materials.required_quantity("item-2") is None
    page.material_scope.setCurrentIndex(0)
    assert owned_materials.quantities()["item-2"] == 4
    assert owned_materials.select_material("item-2")
    assert owned_input.value() == 4
    _dispose_widget(page)


def test_owned_material_result_reuses_editor_on_first_and_changed_materials() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton, QSpinBox, QWidget

    from src.features.toolbox.cultivation.owned_materials import CultivationOwnedMaterials
    from src.services.progression.planner_service import CultivationMaterial

    QApplication.instance() or QApplication([])
    host = QWidget()
    owned = CultivationOwnedMaterials(lambda _item_id: None, host)
    host.show()
    editor = owned.findChild(QSpinBox, "cultivationOwnedMaterialQuantity")
    assert editor is not None

    owned.set_materials((CultivationMaterial("a", "材料 A", 2),))
    assert owned.select_material("a")
    QTest.mouseClick(editor.lineEdit(), Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert editor.lineEdit().selectedText() == editor.text()
    QTest.keyClicks(editor.lineEdit(), "4")
    owned.set_materials((
        CultivationMaterial("b", "材料 B", 3),
        CultivationMaterial("a", "材料 A", 5),
    ))
    assert owned.quantities() == {"b": 0, "a": 4}
    assert owned.required_quantity("a") == 5
    assert owned.findChild(QSpinBox, "cultivationOwnedMaterialQuantity") is editor

    owned.apply_import({"hidden": 9})
    clear = owned.findChild(QPushButton, "cultivationOwnedClear")
    clear.click()
    assert owned.quantities() == {"b": 0, "a": 0}
    assert editor.value() == 0
    assert owned.required_quantity("a") == 5
    assert owned.apply_import({"a": 6}) == 1
    assert owned.quantities()["a"] == 6

    owned.clear_materials()
    assert owned.quantities() == {}
    _dispose_widget(host)


def test_native_material_import_updates_both_drafts_without_overwriting_manual_values() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.owned_material_import import ImportedOwnedMaterials
    from src.services.progression.planner_service import CultivationMaterial

    class Service(PlanningFixture):
        def list_roles(self):
            return ()

    QApplication.instance() or QApplication([])
    identity = {"value": ("a", 1, "dataset")}
    calls = []

    def importer():
        calls.append(True)
        return ImportedOwnedMaterials((("a", 7), ("b", 4)), "2026-09-25T00:00:00+00:00", 1, 0)  # 观测时间遵循正式 UTC 格式。

    page = CultivationCalculatorPage(
        Service(), context_identity=lambda: identity["value"], material_importer=importer,
    )
    single = page.calculator._owned_materials
    batch = page.batch_calculator._owned_materials
    single.set_materials((CultivationMaterial("a", "材料 A", 10),))
    batch.set_materials((CultivationMaterial("a", "材料 A", 10),))
    assert single.select_material("a")
    single._canvas.editor.setValue(2)
    button = page.findChild(QPushButton, "cultivationOwnedImport")
    assert button.isEnabled()
    button.click()
    assert calls == [True]
    assert single.quantities()["a"] == 2
    assert single.quantities()["b"] == 4
    assert batch.quantities()["a"] == 7
    assert batch.quantities()["b"] == 4
    assert "未观测项" in single.findChild(QLabel, "cultivationOwnedImportStatus").text()

    identity["value"] = ("b", 2, "dataset")
    button.click()
    assert calls == [True]
    _dispose_widget(page)


def test_cultivation_mode_switch_preserves_each_draft() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.planner_service import CultivationRole, CultivationSeed

    class Service(PlanningFixture):
        def list_roles(self):
            return tuple(CultivationRole(index, f"角色 {index}") for index in range(1, 4))

        def load_seed(self, character_id):
            return CultivationSeed(character_id, f"角色 {character_id}", 1, 0, (), None)

    QApplication.instance() or QApplication([])
    page = CultivationCalculatorPage(Service())
    page.show()
    QApplication.processEvents()

    batch_mode = page.findChild(QPushButton, "cultivationMultiMode")
    assert batch_mode is not None

    page.calculator._current_level.setValue(37)
    batch_mode.click()
    page.batch_calculator._add_role_by_id(1)
    page.batch_calculator._add_role_by_id(2)
    first = page.batch_calculator._cards[0]
    first.current_level.setValue(12)
    page.findChild(QPushButton, "cultivationSingleMode").click()
    assert page.calculator._current_level.value() == 37
    batch_mode.click()
    assert page.batch_calculator._cards[0].current_level.value() == 12
    _dispose_widget(page)


def test_multi_character_page_calculates_combined_stamina_and_ordered_targets() -> None:
    from PySide6.QtCore import QElapsedTimer, QPoint
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QPushButton,
        QSpinBox,
        QToolButton,
    )
    from shiboken6 import isValid

    from src.domain.progression.stamina import FarmingStage, MaterialYield
    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.character_requirements import MaterialSummaryStatus
    from src.services.progression.planner_service import (
        CultivationMaterial,
        CultivationPlan,
        CultivationRole,
        CultivationSection,
        CultivationSeed,
    )

    class Service(PlanningFixture):
        def list_roles(self):
            return (CultivationRole(1, "角色一"), CultivationRole(2, "角色二"))

        def load_seed(self, character_id):
            return CultivationSeed(character_id, f"角色{character_id}", 1, 0, (), None)

        def calculate(self, request):
            item_id = "a" if request.character_id == 1 else "b"
            material = CultivationMaterial(item_id, f"材料 {item_id}", 1)
            materials = (
                material,
                CultivationMaterial("OrdinaryMonMaterial_test", "野外材料", 2),
                CultivationMaterial("Fons", "方斯", 100),
            ) if request.character_id == 1 else (material,)
            return CultivationPlan(
                f"角色{request.character_id}",
                MaterialSummaryStatus.COMPLETE,
                (CultivationSection("角色升级", materials),),
                materials,
                0,
                0,
                (),
                (),
            )

        def load_farming_stages(self):
            return (FarmingStage(
                "shared",
                "共享材料副本",
                1,
                0,
                40,
                (MaterialYield("a", 1), MaterialYield("b", 1),
                 MaterialYield("Fons", 50)),
                "test",
            ),)

    QApplication.instance() or QApplication([])
    identity = lambda: ("account", 1, "dataset")
    page = CultivationCalculatorPage(Service(), context_identity=identity)
    assert page.material_scope.currentData() == "stamina"
    page.resize(1180, 800)
    page.show()
    page.findChild(QPushButton, "cultivationMultiMode").click()
    batch = page.batch_calculator
    batch._add_role_by_id(1)
    batch._add_role_by_id(2)
    calculate = page.findChild(QPushButton, "cultivationBatchCalculate")
    batch._apply_role_selection((2,))
    batch._apply_role_selection((1, 2))
    assert [card.character_id for card in batch._cards] == [2, 1]

    first_plan = batch._batch_service.calculate(batch._build_request(identity()))
    batch._receive_plan(first_plan)
    batch._has_calculated = True  # Direct result injection stands in for calculate().
    QApplication.processEvents()
    QApplication.processEvents()

    assert batch._last_plan is not None
    assert [target.character_id for target in batch._last_plan.target_plans] == [2, 1]
    assert batch._last_plan.combined_stamina.total_stamina == 40
    assert batch._last_plan.separate_stamina_total == 80
    assert batch._last_plan.saved_stamina == 40
    combined = page.findChild(QFrame, "cultivationBatchCombinedTotals")
    rendered = [label.text() for label in combined.findChildren(QLabel)]
    assert "最低体力 40" in rendered
    assert any("节省 40 体力" in text for text in rendered)
    assert page.copy_button.isEnabled()
    page.material_scope.setCurrentIndex(1)
    assert batch._owned_materials.required_quantity("OrdinaryMonMaterial_test") is None
    assert batch._owned_materials.required_quantity("Fons") is None
    assert batch._owned_materials.required_quantity("a") == 1
    batch.copy_plan()
    assert "野外材料" not in QApplication.clipboard().text()
    page.material_scope.setCurrentIndex(0)
    assert batch._owned_materials.required_quantity("OrdinaryMonMaterial_test") == 2
    assert batch._owned_materials.required_quantity("Fons") == 100
    result = page.findChild(QFrame, "cultivationBatchResult")
    clock = QElapsedTimer()
    clock.start()
    while page.scroll.verticalScrollBar().value() == 0 and clock.elapsed() < 1500:
        QTest.qWait(10)
    result_top = result.mapTo(page.scroll.viewport(), QPoint()).y()
    assert result_top < page.scroll.viewport().height()
    assert page.scroll.verticalScrollBar().value() > 0

    previous_plan = batch._last_plan
    assert batch._owned_materials.select_material("b")
    owned = batch._owned_materials.findChild(QSpinBox, "cultivationOwnedMaterialQuantity")
    assert owned is not None
    batch._recalculate_timer.start()
    assert batch._recalculate_timer.isActive()
    owned.setValue(1)
    assert batch._last_plan is None
    assert not batch._recalculate_timer.isActive()
    assert not page.copy_button.isEnabled()
    batch._draft_changed()
    assert not batch._recalculate_timer.isActive()
    from unittest.mock import patch

    with patch.object(batch._controller, "submit") as submit:
        calculate.click()
        submitted_request = submit.call_args.args[0]
        assert dict(submitted_request.owned_quantities) == batch._owned_materials.quantities()
    second_plan = batch._batch_service.calculate(batch._build_request(identity()))
    batch._receive_plan(second_plan)
    QApplication.processEvents()
    assert batch._last_plan is not previous_plan
    assert batch._owned_materials.findChild(QSpinBox, "cultivationOwnedMaterialQuantity") is owned
    result_panels = [
        panel
        for panel in page.findChildren(QFrame, "cultivationBatchTargetResult")
        if isValid(panel) and not panel.isHidden()
    ]
    first_result = result_panels[0]
    assert first_result.findChildren(QFrame, "cultivationBatchSectionResult") == []
    first_result.findChild(QToolButton, "cultivationBatchTargetResultToggle").click()
    QApplication.processEvents()
    assert first_result.findChildren(QFrame, "cultivationBatchSectionResult")
    target_text = [label.text() for label in first_result.findChildren(QLabel)]
    assert any("已有分配：材料 b × 1" in text for text in target_text)
    batch.reset_draft()
    assert batch._last_plan is None
    assert batch._cards == []
    _dispose_widget(page)


def test_multi_character_page_selects_all_available_roles_without_count_cap() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.planner_service import (
        CultivationRole,
        CultivationSeed,
    )

    class Service(PlanningFixture):
        def list_roles(self):
            return tuple(CultivationRole(index, f"角色 {index}") for index in range(1, 13))

        def load_seed(self, character_id):
            return CultivationSeed(character_id, f"角色 {character_id}", 1, 0, (), None)

    QApplication.instance() or QApplication([])
    page = CultivationCalculatorPage(Service())
    page.findChild(QPushButton, "cultivationMultiMode").click()
    batch = page.batch_calculator
    batch._apply_role_selection(tuple(range(1, 13)))

    add = page.findChild(QPushButton, "cultivationBatchAddRole")
    assert [card.character_id for card in batch._cards] == list(range(1, 13))
    assert add.isEnabled()
    first = batch._cards[0]
    first.current_level.setValue(12)
    batch._apply_role_selection((1, 12))
    assert [card.character_id for card in batch._cards] == [1, 12]
    assert batch._cards[0].current_level.value() == 12
    batch._apply_role_selection(())
    assert batch._cards == []
    assert add.isEnabled()
    _dispose_widget(page)


def test_cultivation_selector_keeps_single_choice_search_and_multi_selection() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton, QWidget

    from src.features.toolbox.cultivation.selectors import (
        CultivationImageSelector,
    )

    QApplication.instance() or QApplication([])
    owner = QWidget()
    single = CultivationImageSelector(
        owner, title="选择弧盘", description="测试",
        options=(("fork-a", "赤红弧盘", None), ("fork-b", "青蓝弧盘", None)),
        selected_id="fork-a",
    )
    cards = {option_id: card for card, option_id, _name in single._cards}
    assert single.selected_id() == "fork-a"
    cards["fork-b"].setChecked(True)
    assert single.selected_id() == "fork-b"
    single._search.setText("qinglan")
    QApplication.processEvents()
    assert cards["fork-a"].isHidden() and not cards["fork-b"].isHidden()
    _dispose_widget(single)
    dialog = CultivationImageSelector(
        owner,
        title="选择角色",
        description="选择本次目标",
        options=(("1", "角色一", None), ("2", "角色二", None), ("3", "角色三", None)),
        selected_id=None,
        selected_ids=("2",),
        multi_select=True,
    )
    assert dialog.selected_ids() == ("2",)
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "全选").click()
    assert set(dialog.selected_ids()) == {"1", "2", "3"}
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "清空").click()
    assert dialog.selected_ids() == ()
    _dispose_widget(dialog)
    _dispose_widget(owner)


def test_multi_character_inline_editor_keeps_draft_and_selected_fork() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox.cultivation.page import CultivationCalculatorPage
    from src.services.progression.planner_service import (
        CultivationForkSeed,
        CultivationRole,
        CultivationSeed,
        CultivationSkill,
    )

    class Service(PlanningFixture):
        def list_roles(self):
            return (CultivationRole(1, "角色一"), CultivationRole(2, "角色二"))

        def load_seed(self, character_id):
            return CultivationSeed(
                character_id,
                f"角色{character_id}",
                70,
                5,
                (CultivationSkill("skill-a", "A", "技能一", 6, 10),),
                CultivationForkSeed("fork-a", "弧盘甲", 71, 6),
            )

    QApplication.instance() or QApplication([])
    page = CultivationCalculatorPage(Service())
    page.resize(1200, 800)
    page.show()
    page.findChild(QPushButton, "cultivationMultiMode").click()
    batch = page.batch_calculator
    batch._add_role_by_id(1)
    batch._add_role_by_id(2)
    first, second = batch._cards

    first.edit.click()
    assert not first.body.isHidden()
    assert first.fork_name.text() == "弧盘甲"
    first._skill_inputs["skill-a"][1].setValue(8)
    first.fork_target_level.setValue(75)
    unchanged_draft = first.request()
    for width in (1100, 1600, 650):
        first.update_available_width(width)
        assert first.request() == unchanged_draft
    second.edit.click()
    assert not second.body.isHidden()
    assert first.body.isHidden()
    assert first.request().skills[0].target_level == 8
    assert first.request().fork.target_level == 75

    first.set_fork_seed(CultivationForkSeed("fork-b", "弧盘乙", 72, 6))
    assert first.fork_name.text() == "弧盘乙"
    assert first.request().fork.fork_id == "fork-b"
    _dispose_widget(page)
