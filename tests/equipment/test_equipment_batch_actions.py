# 验证批量操作仅消费显式选择、锁保护与原子删除，并复用同一投影内的角色详情。
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMenu, QDialogButtonBox, QLabel, QToolButton, QVBoxLayout, QWidget

from src.domain.equipment.loadouts.loadout_management import LoadoutMutation
from src.features.inventory.equipment.display import detail_cache
from src.features.inventory.equipment.assembly import slot_selection_dialog
from src.features.inventory.equipment.display import display_controller as display
from src.features.inventory.equipment.management.batch_selection_dialog import BatchChoice, BatchSelectionDialog
from src.features.inventory.equipment.display.attribute_weights import prepare_attribute_weights
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from src.ui.equipment.equipment_presentation import EquipmentPresentation
from tests.equipment.test_loadout_management import app, database, prepared  # noqa: F401 -- 复用隔离数据库及Qt夹具
from tests.equipment.test_loadout_import_management import game  # noqa: F401 -- 复用游戏快照夹具
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test

NTE_TEST_TIER = "core"


def test_chooser_explicit_selection_filter_all_and_disabled_rows(app):
    dialog = BatchSelectionDialog(
        None,
        [
            BatchChoice(11, "角色甲", "主力"),
            BatchChoice(12, "角色乙", "锁定", enabled=False),
            BatchChoice(13, "角色丙", options=((31, "主力"), (32, "备用")), default_id=31),
        ],
        title="批量选择",
        description="选择目标",
        accept_text="继续",
    )
    try:
        assert dialog.selected_ids() == []
        assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
        dialog.search.setText("甲")
        dialog.set_all(True)
        assert dialog.selected_ids() == [11]
        dialog.search.clear()
        dialog.set_all(True)
        assert dialog.selected_ids() == [11, 31]
        selector = dialog.findChild(QMenu)
        selector.actions()[1].trigger()
        assert dialog.selected_ids() == [11, 32]
        dialog.search.setText("甲")
        dialog.set_all(False)
        assert dialog.selected_ids() == []
        # 空间键可勾选聚焦行；不依赖鼠标点选或装饰结构。
        from PySide6.QtTest import QTest

        dialog.show()
        app.processEvents()
        button = next(button for button in dialog.findChildren(QToolButton) if button.property("batchChoiceKey") == 11)
        button.setFocus()
        QTest.keyClick(button, Qt.Key_Space)
        assert dialog.selected_ids() == [11]
        assert dialog.screen().availableGeometry().contains(dialog.frameGeometry())
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("count", [1, 2])
def test_assembly_always_prompts_even_for_single_slot_roles(monkeypatch, count):
    selections = tuple(
        SimpleNamespace(
            character_id=index,
            role_name=f"角色{index}",
            slot_id=10 + index,
            slot_key="primary",
            slot_name="主力",
            plan={"score": 230.0},
        )
        for index in range(count)
    )
    choose = Mock(return_value=[10])
    monkeypatch.setattr(slot_selection_dialog, "choose_batch_items", choose)
    assert slot_selection_dialog.select_assembly_slot_ids(None, selections) == [10]
    choices = choose.call_args.args[1]
    assert len(choices) == count and choices[0].options == ((10, "主力"),)
    assert choices[0].character_id == 0 and choices[0].option_scores == ((10, 230.0),)


@pytest.mark.parametrize("bad_selection", ["empty", "duplicate", "missing", "locked"])
def test_batch_delete_rejects_invalid_targets_without_partial_write(database, bad_selection):
    account, plans = database
    if bad_selection == "locked":
        with UserDataDao(account.database_path) as dao:
            dao.set_allocation_lock(plans[1], True)
    service, operation, snapshot = prepared(database)
    first, second = (snapshot.plan(plan).slot_id for plan in plans)
    identities = {"empty": (), "duplicate": (first, first), "missing": (first, 99999), "locked": (first, second)}[
        bad_selection
    ]
    with pytest.raises(UserDataValidationError):
        service.mutate(operation, LoadoutMutation("delete_batch", snapshot, slot_ids=identities))
    with UserDataDao(account.database_path) as dao:
        assert {row["plan"]["plan_id"] for row in dao.list_current_loadout_slot_plans()} == set(plans)


def test_switching_roles_reuses_details_and_invalidates_changed_inputs(app, monkeypatch):
    host = QWidget()
    host.equip_content = QWidget(host)
    host.equip_content_layout = QVBoxLayout(host.equip_content)
    host._equipment_mode = "saved"
    host.app_context = SimpleNamespace(generation=1, account=SimpleNamespace(active_account_id="fixture"))
    host.equipment_presentation = SimpleNamespace(roles_db={}, stats_config={})
    rendered = []

    def render(_window, role, _state, *, target_layout):
        rendered.append(role)
        target_layout.addWidget(QLabel(role))

    monkeypatch.setattr(detail_cache, "_render_equip_role", render)
    states = {name: {} for name in "ABCDE"}
    try:
        for name in "ABA":
            detail_cache.show_equipment_role_detail(host, name, states[name])
        assert rendered == ["A", "B"]
        first_panel = host.equip_content_layout.itemAt(0).widget()
        states["A"]["_allocation_locked"] = True
        detail_cache.show_equipment_role_detail(host, "A", states["A"])
        assert rendered == ["A", "B", "A"]
        assert host.equip_content_layout.itemAt(0).widget() is not first_panel
        host.app_context.generation += 1
        detail_cache.show_equipment_role_detail(host, "A", states["A"])
        assert rendered[-2:] == ["A", "A"]
        for name in "BCDE":
            detail_cache.show_equipment_role_detail(host, name, states[name])
        assert list(host._equip_detail_cache) == ["C", "D", "E"]
        detail_cache.clear_equipment_detail_cache(host, keep_visible=True)
        assert not host._equip_detail_cache
        assert host.equip_content_layout.itemAt(0).widget() is not None
        detail_cache.show_equipment_role_detail(host, "E", states["E"], force_refresh=True)
        assert rendered[-2:] == ["E", "E"]
        count = len(rendered)
        detail_cache.show_equipment_role_detail(host, "E", states["E"], force_refresh=True)
        assert len(rendered) == count + 1
    finally:
        host.close()
        host.deleteLater()


def test_attribute_summary_queries_alias_weights_once_without_per_row_rpc():
    presentation = EquipmentPresentation(app_context=None, dialog_parent=None)
    presentation.roles_db = {"角色": {"weights": {"攻击力%": 0.8}}}
    batch = Mock(side_effect=lambda _weights, names: [[0.8 if name == "攻击力%" else 0.0 for name in names]])
    presentation.scoring_engine = SimpleNamespace(
        prepare_weight_queries=batch, flexible_weight=Mock(side_effect=AssertionError("不应逐行请求独立组件"))
    )
    rows = (SimpleNamespace(key="AtkUp", label="攻击力", value=0.5, percent=True, weight_property_ids=("AtkUp",)),)
    state = {
        "_official_attribute_summaries": {"equipment": rows, "character": rows},
        "_official_previous_attribute_summaries": {"equipment": rows},
    }
    weight = prepare_attribute_weights(presentation, "角色", state)
    assert weight("攻击力%", "equipment") == 0.8
    assert weight("攻击力%", "character") == 0.8
    assert batch.call_count == 1


@pytest.mark.parametrize("selected", [None, [], [1004]])
def test_batch_import_only_submits_selected_characters(game, app, monkeypatch, selected):
    from src.features.inventory.equipment.management.persistence_controller import LoadoutOperation
    from src.services.equipment.loadouts.loadout_management import LoadoutManagementService
    from PySide6.QtWidgets import QMessageBox

    account, _native, requests = game
    operation = LoadoutOperation(account)
    snapshot = LoadoutManagementService().prepare(operation)
    host = QWidget()
    host._game_loadout_states = {request.projection.role_name: {"_game_importable": True} for request in requests}
    host.equipment_persistence_controller = SimpleNamespace(
        prepare=lambda callback, _failed: callback(snapshot, operation), commit=Mock()
    )
    monkeypatch.setattr(
        display,
        "_freeze_game_import",
        lambda _host, role, _state: next(row for row in requests if row.projection.role_name == role),
    )
    monkeypatch.setattr(display, "choose_batch_items", lambda *_args, **_kwargs: selected)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    try:
        display._batch_import_game_loadouts(host)
        commit = host.equipment_persistence_controller.commit
        if not selected:
            commit.assert_not_called()
        else:
            commit.assert_called_once()
            mutation = commit.call_args.args[1]
            assert [row.projection.character_id for row in mutation.requests] == selected
            assert mutation.requests[0].create_slot_name == "主力"
    finally:
        host.close()
        host.deleteLater()


@pytest.mark.parametrize("selected", [None, [], "first"])
def test_batch_delete_only_submits_explicit_slots(database, app, monkeypatch, selected):
    from PySide6.QtWidgets import QMessageBox

    service, operation, snapshot = prepared(database)
    first = snapshot.slots[0].slot_id
    identities = [first] if selected == "first" else selected
    host = QWidget()
    host.equipment_persistence_controller = SimpleNamespace(
        prepare=lambda callback, _failed: callback(snapshot, operation), commit=Mock()
    )
    monkeypatch.setattr(display, "choose_batch_items", lambda *_args, **_kwargs: identities)
    monkeypatch.setattr(QMessageBox, "question", lambda *_args: QMessageBox.Yes)
    try:
        display._batch_delete_equipment(host)
        commit = host.equipment_persistence_controller.commit
        if identities:
            mutation = commit.call_args.args[1]
            assert mutation.action == "delete_batch" and mutation.slot_ids == (first,)
        else:
            commit.assert_not_called()
    finally:
        host.close()
        host.deleteLater()


@pytest.mark.parametrize("native", [False, True])
def test_empty_assembly_selection_never_resolves_or_dispatches(native, monkeypatch):
    from src.features.inventory.equipment.assembly import assembly_controller, automatic_assembly_controller

    module = assembly_controller if native else automatic_assembly_controller
    host = SimpleNamespace(user_database_path="unused.sqlite3")
    dao = Mock()
    dao.__enter__ = Mock(return_value=dao)
    dao.__exit__ = Mock(return_value=None)
    service = Mock()
    service.list_current.return_value = (SimpleNamespace(role_name="角色"),)
    monkeypatch.setattr(module, "UserDataDao", lambda *_args: dao)
    monkeypatch.setattr(module, "LoadoutSlotSelectionService", lambda *_args: service)
    monkeypatch.setattr(module, "request_input_entry", lambda *_args: True)
    monkeypatch.setattr(module, "select_assembly_slot_ids", lambda *_args, **_kwargs: [])
    if native:
        start = Mock()
        monkeypatch.setattr(module, "_start_nte_core_equipment_apply", start)
        module._preview_nte_core_assemble_all_roles(host)
    else:
        start = Mock()
        monkeypatch.setattr(module, "_start_automatic_equipment_assembly", start)
        module._preview_automatic_assemble_all_roles(host)
    service.resolve.assert_not_called()
    start.assert_not_called()


def test_native_to_visual_fallback_preserves_exact_selected_slots(monkeypatch):
    from src.features.inventory.equipment.assembly import assembly_controller as module

    host = SimpleNamespace(user_database_path="unused.sqlite3")
    dao = Mock()
    dao.__enter__ = Mock(return_value=dao)
    dao.__exit__ = Mock(return_value=None)
    dao.inventory_snapshot_summary.return_value = {"source": "gamepad"}
    service = Mock()
    service.list_current.return_value = ()
    service.resolve.return_value = (SimpleNamespace(role_name="角色", slot_id=42, plan={"source_snapshot_id": 1}),)
    monkeypatch.setattr(module, "UserDataDao", lambda *_args: dao)
    monkeypatch.setattr(module, "LoadoutSlotSelectionService", lambda *_args: service)
    monkeypatch.setattr(module, "request_input_entry", lambda *_args: True)
    monkeypatch.setattr(module, "select_assembly_slot_ids", lambda *_args, **_kwargs: [42])
    monkeypatch.setattr(module, "_confirm_automatic_assembly_fallback", lambda *_args: True)
    fallback = Mock()
    monkeypatch.setattr(module, "_preview_automatic_assemble_all_roles", fallback)
    module._preview_nte_core_assemble_all_roles(host)
    fallback.assert_called_once_with(host, role_names=["角色"], slot_ids=[42])


@pytest.mark.parametrize("theme", ["black"])
def test_avatar_picker_keeps_actions_reachable_on_small_screens(app, monkeypatch, theme):
    from PySide6.QtCore import QRect, QSize
    from src.app.theme import apply_app_theme, current_theme_name
    from src.app.window_geometry import fit_dialog_to_available_screen
    from src.features.inventory.equipment.management import batch_selection_dialog as module
    from src.integrations.game_assets.catalog import GameUiAssetCatalog

    original = current_theme_name()
    lookup = Mock(wraps=GameUiAssetCatalog.character_icon)
    monkeypatch.setattr(
        module.GameUiAssetCatalog, "character_icon", lambda catalog, identifier: lookup(catalog, identifier)
    )
    apply_app_theme(app, theme)
    dialog = BatchSelectionDialog(
        None,
        [
            BatchChoice(71, "角色甲", character_id=1003, subtitle="主力"),
            BatchChoice(72, "角色甲", character_id=1003, subtitle="备用"),
        ],
        title="选择配装",
        description="请选择目标",
        accept_text="确认选择",
    )
    try:
        assert [call.args[1] for call in lookup.call_args_list] == [1003, 1003]
        assert dialog.minimumWidth() <= dialog.width() and dialog.minimumHeight() <= dialog.height()
        buttons = [
            button for button in dialog.findChildren(QToolButton) if button.property("batchChoiceKey") is not None
        ]
        assert len(buttons) == 2
        avatars = [
            label for label in dialog.findChildren(QLabel) if label.pixmap() is not None and not label.pixmap().isNull()
        ]
        assert len(avatars) == 2
        dialog.show()
        app.processEvents()
        available = QRect(0, 0, 560, 440)
        fit_dialog_to_available_screen(dialog, QSize(920, 700), available_geometry=available)
        app.processEvents()
        assert available.contains(dialog.frameGeometry())
        assert dialog.scroll.horizontalScrollBar().maximum() == 0
        dialog.set_all(True)
        assert dialog.selected_ids() == [71, 72]
        for action in dialog.buttons.buttons():
            position = action.mapTo(dialog, action.rect().center())
            assert dialog.rect().contains(position) and action.isVisible()
        dialog.search.setText("无匹配项")
        assert dialog.empty.isVisible()
        assert dialog.selected_ids() == [71, 72]
    finally:
        dialog.close()
        dialog.deleteLater()
        apply_app_theme(app, original)


def test_switching_slot_updates_frozen_score_and_grade_in_shared_card(app):
    from src.ui.roles.role_selection_card import RoleSelectionCard

    dialog = BatchSelectionDialog(
        None,
        [
            BatchChoice(
                1,
                "角色",
                character_id=1003,
                options=((11, "主力"), (12, "备用"), (13, "旧方案")),
                default_id=11,
                option_scores=((11, 302.36), (12, 155.64), (13, None)),
            )
        ],
        title="装配",
        description="选择计算配装",
        accept_text="确定",
    )
    try:
        card = dialog.findChild(RoleSelectionCard)
        assert card.score_label.text() == "302.36" and card.grade_label.text() == "ACE"
        card.button.click()
        menu = dialog.findChild(QMenu)
        menu.actions()[1].trigger()
        assert dialog.selected_ids() == [12]
        assert card.score_label.text() == "155.64" and card.grade_label.text() == "D"
        menu.actions()[2].trigger()
        assert dialog.selected_ids() == [13]
        assert card.score_label.text() == "评分资料不足" and not card.grade_label.text()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_delete_snapshot_uses_complete_frozen_item_scores_not_stale_total(database):
    _service, _operation, snapshot = prepared(database)
    # 夹具的旧总分为0，逐件冻结分为20；与计算配装详情采用相同完整分数来源。
    assert all(slot.score == 20.0 for slot in snapshot.slots)


def test_game_import_picker_shows_game_score_not_calculation_score(game, app, monkeypatch):
    from src.features.inventory.equipment.management.persistence_controller import LoadoutOperation
    from src.services.equipment.loadouts.loadout_management import LoadoutManagementService

    account, _native, requests = game
    operation = LoadoutOperation(account)
    snapshot = LoadoutManagementService().prepare(operation)
    host = QWidget()
    host._game_loadout_states = {request.projection.role_name: {"_game_importable": True} for request in requests}
    host.equipment_persistence_controller = SimpleNamespace(
        prepare=lambda callback, _failed: callback(snapshot, operation), commit=Mock()
    )
    monkeypatch.setattr(
        display,
        "_freeze_game_import",
        lambda _host, role, _state: next(row for row in requests if row.projection.role_name == role),
    )
    choose = Mock(return_value=None)
    monkeypatch.setattr(display, "choose_batch_items", choose)
    try:
        display._batch_import_game_loadouts(host)
        choices = choose.call_args.args[1]
        assert all(choice.score_source == "game" and choice.score == 20.0 for choice in choices)
        host.equipment_persistence_controller.commit.assert_not_called()
    finally:
        host.close()
        host.deleteLater()


def test_score_sort_uses_displayed_slot_score_and_keeps_selection_and_filter(app):
    from src.ui.roles.role_selection_card import RoleSelectionCard
    from src.ui.roles.role_selection_sort import RoleSelectionSort

    dialog = BatchSelectionDialog(
        None,
        [
            BatchChoice(1, "未知角色"),
            BatchChoice(2, "角色乙", score=100),
            BatchChoice(
                3, "角色甲", options=((31, "主力"), (32, "备用")), default_id=31, option_scores=((31, 300), (32, 50))
            ),
            BatchChoice(4, "零分角色", score=0),
        ],
        title="选择",
        description="按分数选择",
        accept_text="确定",
    )

    def visual_order():
        app.processEvents()
        cards = [card for card in dialog.findChildren(RoleSelectionCard) if not card.isHidden()]
        return [card.button.property("batchChoiceKey") for card in sorted(cards, key=lambda card: (card.y(), card.x()))]

    try:
        dialog.show()
        assert visual_order() == [3, 2, 4, 1]
        card = next(
            card for card in dialog.findChildren(RoleSelectionCard) if card.button.property("batchChoiceKey") == 3
        )
        card.button.click()
        card.menu.actions()[1].trigger()
        assert visual_order() == [2, 3, 4, 1]
        assert dialog.selected_ids() == [32]
        selector = dialog.findChild(RoleSelectionSort)
        selector.setCurrentIndex(selector.findData("score_asc"))
        assert visual_order() == [4, 3, 2, 1]
        dialog.search.setText("角色乙")
        selector.setCurrentIndex(selector.findData("score_desc"))
        assert visual_order() == [2]
        assert dialog.selected_ids() == [32]
        dialog.search.clear()
        assert visual_order() == [2, 3, 4, 1]
    finally:
        dialog.close()
        dialog.deleteLater()
