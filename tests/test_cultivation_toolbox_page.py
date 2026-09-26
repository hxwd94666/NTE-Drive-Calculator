# 测试养成计算器在工具页内的整页导航与账号草稿生命周期。
from __future__ import annotations

import os
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_cultivation_uses_one_verified_role_catalog_for_data_and_images(tmp_path) -> None:
    from src.app.context import ApplicationPaths
    from src.integrations.role_catalog_release import RoleCatalogRelease

    paths = ApplicationPaths.from_roots(
        root=tmp_path,
        app_dir=tmp_path,
        data_root=tmp_path,
        bundled_config_dir=tmp_path / "config",
        asset_dir=tmp_path / "assets",
        app_icon_path=tmp_path / "assets" / "app_icon.ico",
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

    class Service:
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


def test_cultivation_page_uses_one_vertical_scroll_surface() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QScrollArea

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage

    class Service:
        def list_roles(self):
            return ()

    QApplication.instance() or QApplication([])
    page = CultivationCalculatorPage(Service())
    page.resize(1120, 740)
    page.show()
    QApplication.processEvents()

    assert page.findChildren(QScrollArea) == [page.scroll]
    assert page.scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert page.scroll.horizontalScrollBar().maximum() == 0
    _dispose_widget(page)


def test_cultivation_owned_materials_deduct_and_keep_five_columns() -> None:
    from types import SimpleNamespace

    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtWidgets import (
        QApplication,
        QFrame,
        QLabel,
        QLineEdit,
        QPushButton,
        QSpinBox,
    )

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage
    from src.services.character_progression_requirements import MaterialSummaryStatus
    from src.services.cultivation_planner_service import (
        CultivationMaterial,
        CultivationPlan,
        CultivationRole,
        CultivationSection,
        CultivationSeed,
    )
    from src.ui.progression_material_card import ProgressionMaterialCard

    materials = tuple(
        CultivationMaterial(f"item-{index}", f"材料 {index}", index * 10)
        for index in range(1, 6)
    )

    class Service:
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
    assert page.calculator._material_scope == "stamina"
    assert page.batch_calculator._material_scope == "stamina"
    page.material_scope.setCurrentIndex(0)
    page.resize(1180, 760)
    page.show()
    page.calculator._calculate()
    QApplication.processEvents()
    assert service.stamina_calls == 1

    sync = page.findChild(QPushButton, "cultivationOwnedImport")
    assert sync is not None and not sync.isEnabled()
    assert sync.text() == "同步材料"
    clear = page.findChild(QPushButton, "cultivationOwnedClear")
    assert clear is not None and clear.isEnabled()
    owned_materials = page.calculator._owned_materials
    owned_inputs = owned_materials.findChildren(QSpinBox, "cultivationOwnedMaterialQuantity")
    assert len(owned_inputs) == 1
    numeric_inputs = page.findChildren(QSpinBox)
    assert numeric_inputs
    assert all(
        not control.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        for control in numeric_inputs
    )
    assert all(
        not editor.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
        for control in numeric_inputs
        for editor in control.findChildren(QLineEdit)
    )
    assert owned_materials.select_material("item-1")
    owned_inputs[0].setValue(3)
    assert owned_materials.quantities()["item-1"] == 3
    assert service.stamina_calls == 1
    assert not page.copy_button.isEnabled()
    assert page.calculator._calculate_button.text() == "重新计算所需材料与体力"
    assert not any(
        not frame.isHidden()
        for frame in page.findChildren(QFrame, "cultivationCalculatorTotals")
    )
    owned_inputs[0].setFocus()
    class WheelEvent:
        ignored = False

        def ignore(self):
            self.ignored = True

    wheel_event = WheelEvent()
    owned_inputs[0].wheelEvent(wheel_event)
    assert wheel_event.ignored
    assert owned_inputs[0].value() == 3
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
    assert owned_materials.findChild(QSpinBox, "cultivationOwnedMaterialQuantity") is owned_inputs[0]
    assert owned_inputs[0].value() == 3
    assert owned_materials.required_quantity("item-1") == 11

    page.calculator._calculate_button.click()
    QApplication.processEvents()
    QApplication.processEvents()
    assert service.stamina_calls == 2
    bar = page.scroll.verticalScrollBar()
    owned_top = owned_materials.mapTo(page.mode_stack, QPoint(0, 0)).y()
    assert bar.value() == min(bar.maximum(), max(0, owned_top - 12))
    assert page.copy_button.isEnabled()
    assert page.calculator._calculate_button.text() == "计算所需材料与体力"

    totals = next(
        frame
        for frame in page.findChildren(QFrame, "cultivationCalculatorTotals")
        if not frame.isHidden()
    )
    total_cards = totals.findChildren(ProgressionMaterialCard)
    assert len(total_cards) == 5
    rendered = [label.text() for label in totals.findChildren(QLabel)]
    assert "× 7" in rendered
    assert "最低体力 120" in rendered
    badges = page.findChildren(QLabel, "cultivationStaminaBadge")
    assert [badge.text() for badge in badges] == ["最低体力 120", "最低体力 120"]
    page.material_scope.setCurrentIndex(1)
    QApplication.processEvents()
    assert service.stamina_calls == 2
    assert owned_materials.required_quantity("item-1") == 10
    assert owned_materials.required_quantity("item-2") is None
    scoped_totals = next(
        frame for frame in page.findChildren(QFrame, "cultivationCalculatorTotals")
        if not frame.isHidden()
    )
    assert len(scoped_totals.findChildren(ProgressionMaterialCard)) == 2
    page.calculator.copy_plan()
    assert "材料 1" in QApplication.clipboard().text()
    assert "材料 2" not in QApplication.clipboard().text()
    page.material_scope.setCurrentIndex(0)
    assert owned_materials.select_material("item-2")
    owned_inputs[0].setValue(4)
    assert not page.copy_button.isEnabled()
    page.material_scope.setCurrentIndex(1)
    assert owned_materials.required_quantity("item-2") is None
    page.material_scope.setCurrentIndex(0)
    assert owned_materials.quantities()["item-2"] == 4
    assert owned_materials.select_material("item-2")
    assert owned_inputs[0].value() == 4
    _dispose_widget(page)


def test_owned_material_result_reuses_editor_on_first_and_changed_materials() -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QAbstractSpinBox, QPushButton, QSpinBox, QWidget

    from src.features.toolbox.cultivation_owned_materials import CultivationOwnedMaterials
    from src.services.cultivation_planner_service import CultivationMaterial

    QApplication.instance() or QApplication([])
    host = QWidget()
    owned = CultivationOwnedMaterials(lambda _item_id: None, host)
    host.show()
    editor = owned.findChild(QSpinBox, "cultivationOwnedMaterialQuantity")
    assert editor is not None
    assert editor.buttonSymbols() == QAbstractSpinBox.ButtonSymbols.NoButtons
    editors_before = tuple(owned.findChildren(QSpinBox))

    owned.set_materials((CultivationMaterial("a", "材料 A", 2),))
    assert tuple(owned.findChildren(QSpinBox)) == editors_before
    assert owned.select_material("a")
    QTest.mouseClick(editor.lineEdit(), Qt.MouseButton.LeftButton)
    QApplication.processEvents()
    assert editor.lineEdit().selectedText() == editor.text()
    editor.setValue(4)
    owned.set_materials((
        CultivationMaterial("b", "材料 B", 3),
        CultivationMaterial("a", "材料 A", 5),
    ))
    assert owned.quantities() == {"b": 0, "a": 4}
    assert owned.required_quantity("a") == 5
    assert owned.findChild(QSpinBox, "cultivationOwnedMaterialQuantity") is editor
    assert tuple(owned.findChildren(QSpinBox)) == editors_before

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
    assert tuple(owned.findChildren(QSpinBox)) == editors_before
    _dispose_widget(host)


def test_native_material_import_updates_both_drafts_without_overwriting_manual_values() -> None:
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage
    from src.services.cultivation_owned_material_import import ImportedOwnedMaterials
    from src.services.cultivation_planner_service import CultivationMaterial

    class Service:
        def list_roles(self):
            return ()

    QApplication.instance() or QApplication([])
    identity = {"value": ("a", 1, "dataset")}
    calls = []

    def importer():
        calls.append(True)
        return ImportedOwnedMaterials((("a", 7), ("b", 4)), "2026-09-25", 1, 0)

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
    assert "未观测项" in single._import_status.text()

    identity["value"] = ("b", 2, "dataset")
    button.click()
    assert calls == [True]
    _dispose_widget(page)


def test_cultivation_page_removes_saved_state_note_and_switches_mode_drafts() -> None:
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage
    from src.services.cultivation_planner_service import CultivationRole, CultivationSeed

    class Service:
        def list_roles(self):
            return tuple(CultivationRole(index, f"角色 {index}") for index in range(1, 4))

        def load_seed(self, character_id):
            return CultivationSeed(character_id, f"角色 {character_id}", 1, 0, (), None)

    QApplication.instance() or QApplication([])
    page = CultivationCalculatorPage(Service())
    page.show()
    QApplication.processEvents()

    texts = [label.text() for label in page.findChildren(QLabel)]
    assert not any("按角色页已保存的养成状态" in text for text in texts)
    batch_mode = page.findChild(QPushButton, "cultivationMultiMode")
    assert batch_mode is not None and batch_mode.text() == "多角色"
    assert not page.findChildren(QLabel, "cultivationMultiModeFuture")

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

    from src.domain.progression_stamina import FarmingStage, MaterialYield
    from src.features.toolbox.cultivation_page import CultivationCalculatorPage
    from src.services.character_progression_requirements import MaterialSummaryStatus
    from src.services.cultivation_planner_service import (
        CultivationMaterial,
        CultivationPlan,
        CultivationRole,
        CultivationSection,
        CultivationSeed,
    )

    class Service:
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
    batch._set_busy(True)
    assert calculate.text() == "正在计算中"
    assert not calculate.isEnabled()
    batch._set_busy(False)
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
    assert calculate is not None and calculate.text() == "计算多角色材料与体力"
    assert page.findChild(QLabel, "cultivationBatchStatus") is None
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
    assert calculate.text() == "重新计算多角色材料与体力"
    batch._draft_changed()
    assert not batch._recalculate_timer.isActive()
    from unittest.mock import patch

    with patch.object(batch._controller, "submit") as submit:
        calculate.click()
        submit.assert_called_once()
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
    from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QSpinBox, QToolButton

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage
    from src.services.cultivation_planner_service import (
        CultivationMaterial,
        CultivationRole,
        CultivationSeed,
    )

    class Service:
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
    counts = [label.text() for label in page.findChildren(QLabel)]
    assert len(batch._cards) == 12
    assert add.isEnabled()
    assert add.text() == "选择角色"
    assert "已选 12 名" in counts
    assert not any(
        button.text() in {"上移", "下移"}
        for kind in (QPushButton, QToolButton)
        for button in page.findChildren(kind)
    )
    batch._owned_materials.set_materials((CultivationMaterial("a", "材料 A", 1),))
    assert batch._owned_materials.select_material("a")
    owned_input = batch._owned_materials.findChild(QSpinBox, "cultivationOwnedMaterialQuantity")
    owned_input.setValue(4)
    class WheelEvent:
        ignored = False

        def ignore(self):
            self.ignored = True

    wheel_event = WheelEvent()
    owned_input.wheelEvent(wheel_event)
    assert wheel_event.ignored and owned_input.value() == 4
    first = batch._cards[0]
    first.current_level.setValue(12)
    batch._apply_role_selection((1, 12))
    assert [card.character_id for card in batch._cards] == [1, 12]
    assert batch._cards[0] is first
    assert first.current_level.value() == 12
    batch._apply_role_selection(())
    assert batch._cards == []
    assert add.isEnabled()
    _dispose_widget(page)


def test_multi_character_selector_preselects_and_supports_all_clear() -> None:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget
    from unittest.mock import patch

    from src.features.toolbox.cultivation_selectors import (
        CultivationImageSelector,
        select_cultivation_items,
    )

    QApplication.instance() or QApplication([])
    owner = QWidget()
    dialog = CultivationImageSelector(
        owner,
        title="选择角色",
        description="选择本次目标",
        options=(("1", "角色一", None), ("2", "角色二", None), ("3", "角色三", None)),
        selected_id=None,
        selected_ids=("2",),
        multi_select=True,
    )
    assert not dialog._search.testAttribute(Qt.WidgetAttribute.WA_InputMethodEnabled)
    assert dialog.selected_ids() == ("2",)
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "全选").click()
    assert dialog.selected_ids() == ("1", "2", "3")
    next(button for button in dialog.findChildren(QPushButton) if button.text() == "清空").click()
    assert dialog.selected_ids() == ()
    _dispose_widget(dialog)
    with patch.object(
        CultivationImageSelector, "exec", return_value=QDialog.DialogCode.Accepted
    ):
        selected = select_cultivation_items(
            owner,
            title="选择角色",
            description="选择本次目标",
            options=(("1", "角色一", None),),
            selected_ids=("1",),
        )
    assert selected == ("1",)
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not owner.findChildren(CultivationImageSelector)
    _dispose_widget(owner)


def test_multi_character_inline_editor_keeps_draft_and_updates_fork_image(tmp_path) -> None:
    import json

    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication, QPushButton

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage
    from src.services.cultivation_planner_service import (
        CultivationForkSeed,
        CultivationRole,
        CultivationSeed,
        CultivationSkill,
    )

    for name, color in (("first.png", "#ff8844"), ("second.png", "#5588ff")):
        image = QImage(80, 80, QImage.Format.Format_ARGB32)
        image.fill(QColor(color))
        assert image.save(str(tmp_path / name))
    (tmp_path / "manifest.json").write_text(json.dumps({
        "fork_items": {"fork-a": "first.png", "fork-b": "second.png"},
    }), encoding="utf-8")

    class Service:
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
    page = CultivationCalculatorPage(Service(), asset_root=tmp_path)
    page.resize(1200, 800)
    page.show()
    page.findChild(QPushButton, "cultivationMultiMode").click()
    batch = page.batch_calculator
    batch._add_role_by_id(1)
    batch._add_role_by_id(2)
    first, second = batch._cards

    first.edit.click()
    assert first.body.isVisible()
    assert first.fork_name.text() == "弧盘甲"
    assert not first.fork_icon.pixmap().isNull()
    first._skill_inputs["skill-a"][1].setValue(8)
    first.fork_target_level.setValue(75)
    unchanged_draft = first.request()
    for width in (1100, 1600, 650):
        first.update_available_width(width)
        assert first.request() == unchanged_draft
    second.edit.click()
    assert second.body.isVisible()
    assert not first.body.isVisible()
    assert first.request().skills[0].target_level == 8
    assert first.request().fork.target_level == 75

    first.set_fork_seed(CultivationForkSeed("fork-b", "弧盘乙", 72, 6))
    assert first.fork_name.text() == "弧盘乙"
    assert first.fork_button.text() == "更换弧盘"
    assert first.request().fork.fork_id == "fork-b"
    _dispose_widget(page)
