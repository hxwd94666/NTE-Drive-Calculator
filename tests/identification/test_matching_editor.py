# 验证共用套装编辑的草稿、账号切换、持久化及小屏可操作性，不操作游戏。
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from PySide6.QtCore import QRect, QSize, Qt, QItemSelectionModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QWidget

from src.app.theme import apply_app_theme, current_theme_name
from src.app.window_geometry import fit_dialog_to_available_screen
from src.integrations.preferences.locale_files import LocaleFiles
from src.services.identification.matching_settings import MatchingSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.role_suit_matching.controller import RoleSuitMatchingController
from src.ui.role_suit_matching.dialog import MatchingEditorDialog


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def catalog(tmp_path):
    return LocaleFiles(Path("assets/locales"), tmp_path / "patches").base


def wait_until(predicate):
    for _ in range(300):
        QApplication.processEvents()
        if predicate():
            return
        QTest.qWait(10)
    raise AssertionError("UI task did not settle")


def context(tmp_path):
    database = tmp_path / "account.sqlite3"
    with UserDataDao(database, account_id="fixture"):
        pass
    return SimpleNamespace(account=SimpleNamespace(active_account_id="fixture", user_database_path=database),
        generation=1, paths=SimpleNamespace(config_dir=Path("config"), equipment_allocation_database_path=Path("data/game_static.sqlite3"), game_ui_asset_root=tmp_path / "art"),
        register_account_lifecycle=lambda _lifecycle: lambda: None)


def data(settings):
    return ((1001, "角色甲", "A"), (1002, "角色乙", "B")), (("A", "套装甲"), ("B", "套装乙")), settings, settings, {"roles": {}, "suits": {}}




def test_shared_owner_saves_once_notifies_and_cancelled_reopen_preserves_value(app, tmp_path):
    ctx = context(tmp_path)
    service = MatchingSettingsService(ctx.account.user_database_path)
    parent = QWidget()
    controller = RoleSuitMatchingController(ctx, catalog(tmp_path), parent)
    notifications = []
    controller.changed.connect(notifications.append)
    def load(request, _cancel):
        return data(MatchingSettingsService(request.database).load())
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", side_effect=load), \
             patch("src.ui.role_suit_matching.controller.preview_matching_role", return_value={"A": "matched"}):
            controller.open(parent)
            wait_until(lambda: not controller.is_running())
            dialog = controller._dialog
            controller.open(parent)
            assert controller._dialog is dialog
            dialog.draft["extra_suits"] = {"1001": ["B"]}
            dialog.buttons.button(QDialogButtonBox.Save).click()
            wait_until(lambda: not controller.is_running() and controller._dialog is None)
            assert service.load()["extra_suits"] == {"1001": ["B"]}
            assert len(notifications) == 1 and notifications[0].generation == 1
            controller.open(parent)
            wait_until(lambda: not controller.is_running())
            controller._dialog.role_list.itemClicked.emit(controller._dialog.role_list.item(0))
            controller._dialog.reset.click()
            controller._dialog.close()
            wait_until(lambda: not controller.is_running())
            assert service.load()["extra_suits"] == {"1001": ["B"]}
            assert len(notifications) == 1
    finally:
        controller.close()
        wait_until(lambda: not controller.is_running())
        parent.close()
        parent.deleteLater()
        app.processEvents()


def test_rapid_choices_only_check_latest_draft_and_return_cancels_pending_check(app, tmp_path):
    ctx = context(tmp_path)
    parent = QWidget()
    owner = RoleSuitMatchingController(ctx, catalog(tmp_path), parent)
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor",
                   return_value=data({"version": 1, "extra_suits": {}})), \
             patch("src.ui.role_suit_matching.controller.preview_matching_role", return_value={"B": "matched"}) as check:
            owner.open(parent)
            wait_until(lambda: not owner.is_running())
            dialog = owner._dialog
            dialog.role_list.itemClicked.emit(dialog.role_list.item(0))
            extra = next(dialog.suit_list.item(i) for i in range(dialog.suit_list.count())
                         if dialog.suit_list.item(i).data(Qt.UserRole) == "B")
            for selected in (True, False, True, False, True):
                extra.setSelected(selected)
            wait_until(lambda: not owner.is_running())
            assert check.call_count == 1
            assert check.call_args.args[1] == dialog.draft
            extra.setSelected(False)
            dialog.buttons.button(QDialogButtonBox.Cancel).click()
            QTest.qWait(180)
            app.processEvents()
            assert check.call_count == 1 and dialog.stack.currentIndex() == 0
            assert MatchingSettingsService(ctx.account.user_database_path).load()["extra_suits"] == {}
            dialog.role_list.itemClicked.emit(dialog.role_list.item(0))
            owner.stop()
            QTest.qWait(180)
            app.processEvents()
            assert check.call_count == 1 and owner._dialog is None
    finally:
        owner.close()
        wait_until(lambda: not owner.is_running())
        parent.deleteLater()
        app.processEvents()


def test_account_invalidation_discards_slow_read_and_never_saves_new_account(app, tmp_path):
    ctx = context(tmp_path)
    parent = QWidget()
    controller = RoleSuitMatchingController(ctx, catalog(tmp_path), parent)
    started, released = Event(), Event()
    def read(request, cancel):
        started.set()
        assert released.wait(3)
        return data({"version": 1, "extra_suits": {"1001": ["B"]}})
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", side_effect=read):
            controller.open(parent)
            wait_until(started.is_set)
            controller.stop()
            ctx.generation += 1
            released.set()
            wait_until(lambda: not controller.is_running())
            assert controller._dialog is None
            assert MatchingSettingsService(ctx.account.user_database_path).load()["extra_suits"] == {}
    finally:
        released.set()
        controller.close()
        wait_until(lambda: not controller.is_running())
        parent.deleteLater()
        app.processEvents()


def test_initial_read_failure_disables_save_and_conflict_preserves_both_drafts(app, tmp_path):
    ctx = context(tmp_path)
    parent = QWidget()
    controller = RoleSuitMatchingController(ctx, catalog(tmp_path), parent)
    service = MatchingSettingsService(ctx.account.user_database_path)
    notifications = []
    controller.changed.connect(notifications.append)
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", side_effect=ValueError("fixture")):
            controller.open(parent)
            wait_until(lambda: not controller.is_running())
            assert not controller._dialog.buttons.button(QDialogButtonBox.Save).isEnabled()
            assert controller._dialog.buttons.button(QDialogButtonBox.Cancel).isEnabled()
            controller._dialog.reject()
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", side_effect=lambda *_: data(service.load())), \
             patch("src.ui.role_suit_matching.controller.preview_matching_role", return_value={"A": "matched"}):
            controller.open(parent)
            wait_until(lambda: not controller.is_running())
            dialog = controller._dialog
            draft = {"1001": ["B"]}
            dialog.draft["extra_suits"] = draft.copy()
            external = {"version": 1, "extra_suits": {"1002": ["A"]}}
            service.save(external, expected=service.load())
            dialog.buttons.button(QDialogButtonBox.Save).click()
            wait_until(lambda: not controller.is_running())
            assert controller._dialog is dialog and dialog.draft["extra_suits"] == draft
            assert service.load() == external and notifications == []
            assert dialog.status.text() == controller.catalog.render("matching.save_failed")
    finally:
        controller.close()
        wait_until(lambda: not controller.is_running())
        parent.deleteLater()
        app.processEvents()


def test_picture_selection_unknown_cleanup_search_and_resets_are_draft_only(app, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    settings = {"version": 1, "extra_suits": {"1001": ["unknown"], "1002": ["A"]}}
    roles, suits, stored, projected, artwork = data(settings)
    dialog = MatchingEditorDialog(catalog(tmp_path))
    dialog.load((roles, (*suits, ("C", "套装丙")), stored, projected, artwork))
    def choose(identity, selected):
        item = next(dialog.suit_list.item(i) for i in range(dialog.suit_list.count())
                    if dialog.suit_list.item(i).data(Qt.UserRole) == identity)
        item.setSelected(selected)
    try:
        assert dialog.stack.currentIndex() == 0 and dialog.role_list.count() == 2
        dialog.role_list.itemClicked.emit(dialog.role_list.item(0))
        choose("unknown", False)
        choose("B", True)
        choose("C", True)
        choose("A", False)
        assert dialog.draft["suit_overrides"]["1001"] == ["B", "C"]
        dialog.suit_search.setText("套装乙")
        assert dialog.draft["suit_overrides"]["1001"] == ["B", "C"]
        choose("B", False)
        choose("C", False)  # 最后一项不可清空；恢复按钮才恢复默认。
        assert dialog.draft["suit_overrides"]["1001"] == ["C"]
        dialog.reset.click()
        assert "1001" not in dialog.draft["suit_overrides"]
        assert dialog.draft["extra_suits"] == {"1002": ["A"]}
        dialog.show_overview()
        dialog.search.setText("不存在的角色")
        assert dialog.role_list.count() == 0
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Cancel):
            dialog.reset_all.click()
        assert dialog.draft["extra_suits"] == {"1002": ["A"]}
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            dialog.reset_all.click()
        assert dialog.draft == {"version": 1, "extra_suits": {}}
        dialog.reject()
        assert settings["extra_suits"]["1001"] == ["unknown"]
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def test_projected_avatar_noop_save_keeps_original_identity_and_results(app, tmp_path):
    ctx = context(tmp_path)
    service = MatchingSettingsService(ctx.account.user_database_path)
    stored = {"version": 1, "extra_suits": {"1046": ["B"]}}
    projected = {"version": 1, "extra_suits": {"1051": ["B"]}}
    service.save(stored, expected=service.load())
    parent = QWidget()
    owner = RoleSuitMatchingController(ctx, catalog(tmp_path), parent)
    notifications = []
    owner.changed.connect(notifications.append)
    payload = (((1051, "主角", "A"),), (("A", "套装甲"), ("B", "套装乙")), stored, projected, {"roles": {}, "suits": {}})
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", return_value=payload), \
             patch("src.ui.role_suit_matching.controller.preview_matching_role", return_value={"A": "matched", "B": "matched"}):
            owner.open(parent)
            wait_until(lambda: not owner.is_running())
            owner._dialog.buttons.button(QDialogButtonBox.Save).click()
            wait_until(lambda: not owner.is_running() and owner._dialog is None)
        assert service.load() == stored and notifications == []
    finally:
        owner.close()
        wait_until(lambda: not owner.is_running())
        parent.deleteLater()
        app.processEvents()


def test_result_details_expand_all_suits_and_positions_without_duplicate_score(app, tmp_path):
    from PySide6.QtWidgets import QLabel, QToolButton
    from src.domain.equipment.matching import EquipmentMatchContext, RoleSuitMatch
    from src.features.identification.page import build_identify_result_row
    from src.models.equipment import Drive
    from src.optimizer.scoring import ScoringEngine
    from src.services.identification.equipment_service import EquipmentIdentificationService

    source = SimpleNamespace(roles_db={"测试角色": {"weights": {"暴击率%": 1}}})
    context = EquipmentMatchContext(matches=(
        RoleSuitMatch("测试角色", 1001, "A", "套装甲", True, frozenset(["H_3"]), frozenset(["H_3"])),
        RoleSuitMatch("测试角色", 1001, "B", "套装乙", False, frozenset(), frozenset(["H_3"]))))
    service = EquipmentIdentificationService(source, context, ScoringEngine("config", roles_db=source.roles_db))
    drive = Drive(uid="fixture", quality="Gold", area=3, shape_id="H_3", set_name="",
                  main_stats={"攻击力": 10, "生命值": 100}, sub_stats={})
    rows = service.identify_item(drive)["rows"]
    assert len(rows) == 1 and rows[0]["matched_suits"][0]["positions"] == ["set", "extra"]
    row = build_identify_result_row(1, rows[0], game_ui_asset_root=tmp_path, catalog=catalog(tmp_path))
    try:
        row.show()
        app.processEvents()
        toggle = row.findChild(QToolButton)
        details = next(label for label in row.findChildren(QLabel) if "套装乙 · 自选匹配" in label.text())
        assert not details.isVisible()
        # 默认行只保留角色、评分和展开入口，不再重复展示套装摘要或占比。
        assert not any("套装甲" in label.text() for label in row.findChildren(QLabel) if label.isVisible())
        assert not any("占比" in label.text() for label in row.findChildren(QLabel))
        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key_Space)
        assert details.isVisible() and "套装甲 · 默认匹配 · 套装位 / 散件位" in details.text()
        QTest.keyClick(toggle, Qt.Key_Space)
        assert not details.isVisible()
    finally:
        row.close()
        row.deleteLater()
        app.processEvents()


def test_editor_actions_and_keyboard_navigation_remain_reachable_on_small_screen(app, tmp_path):
    previous = current_theme_name()
    apply_app_theme(app, "black")
    dialog = MatchingEditorDialog(catalog(tmp_path))
    try:
        dialog.load(data({"version": 1, "extra_suits": {"1001": ["B"]}}))
        fit_dialog_to_available_screen(dialog, QSize(610, 440), available_geometry=QRect(0, 0, 760, 560))
        dialog.show()
        app.processEvents()
        for button in dialog.findChildren(QPushButton):
            if button.isVisible():
                assert not button.visibleRegion().isEmpty(), button.text()
        assert dialog.geometry().right() < 760 and dialog.geometry().bottom() < 560
        dialog.search.setFocus()
        QTest.keyClicks(dialog.search, "1002")
        assert dialog.role_list.count() == 1
        assert dialog.role_list.item(0).data(Qt.UserRole) == 1002
        dialog.role_list.setCurrentRow(0)
        dialog.role_list.setFocus()
        QTest.keyClick(dialog.role_list, Qt.Key_Return)
        assert dialog.stack.currentIndex() == 1
        assert dialog.suit_list.isVisible() and not dialog.suit_list.visibleRegion().isEmpty()
        assert dialog.reset.isVisible() and not dialog.reset.visibleRegion().isEmpty()
        assert dialog.suit_search.isVisible() and not dialog.suit_search.visibleRegion().isEmpty()
        dialog.show_preview({"B": "matched"})
        assert not dialog.status.text()
        dialog.show_preview({"B": "unavailable"})
        assert dialog.status.text() and dialog.status.isVisible()
        extra = next(dialog.suit_list.item(i) for i in range(dialog.suit_list.count())
                     if dialog.suit_list.item(i).data(Qt.UserRole) == "A")
        point = dialog.suit_list.visualItemRect(extra).center()
        QTest.mouseClick(dialog.suit_list.viewport(), Qt.LeftButton, pos=point)
        assert extra.isSelected()
        QTest.mouseClick(dialog.suit_list.viewport(), Qt.LeftButton, pos=point)
        assert not extra.isSelected() and "1002" not in dialog.draft["extra_suits"]
        dialog.suit_list.setCurrentItem(extra, QItemSelectionModel.NoUpdate)
        QTest.keyClick(dialog.suit_list, Qt.Key_Space)
        assert dialog.draft["extra_suits"].get("1002") == ["A"]
        dialog.buttons.button(QDialogButtonBox.Cancel).click()
        assert dialog.isVisible() and dialog.stack.currentIndex() == 0
        assert dialog.draft["extra_suits"].get("1002") == ["A"]
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert not dialog.isVisible()
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        apply_app_theme(app, previous)


@pytest.mark.parametrize("comparison", [False, True])
def test_three_actual_entry_views_open_same_owner_and_old_result_is_marked_stale(app, tmp_path, comparison):
    from PySide6.QtCore import QObject, Signal
    from PySide6.QtWidgets import QGroupBox, QVBoxLayout, QDialog, QLabel, QScrollArea
    from src.features.identification.controller import IdentificationController
    from src.features.scanning.post_action_dialog import ScanPostActionDialog
    from src.features.inventory.warehouse.identification_controller import (
        show_warehouse_identification_dialog, show_warehouse_identification_comparison,
    )

    class Matching(QObject):
        changed = Signal(object)
        def __init__(self):
            super().__init__()
            self.catalog = catalog(tmp_path)
            self.parents = []
        def open(self, parent): self.parents.append(parent)
        def create_panel(self, parent):
            self.parents.append(parent)
            panel = MatchingEditorDialog(self.catalog, parent, embedded=True)
            panel.load(data({"version": 1, "extra_suits": {}}))
            return panel

    matching = Matching()
    parent = QWidget()
    ctx = context(tmp_path)
    parent.app_context = ctx
    parent.role_suit_matching = matching
    ctx.account.account_data_root = tmp_path
    ctx.account.screenshot_dir = tmp_path / "shots"
    ctx.account.user_config_dir = tmp_path / "config"
    def card(title):
        group = QGroupBox(title)
        QVBoxLayout(group)
        return group
    owner = IdentificationController(app_context=ctx, dialog_parent=parent, role_suit_matching=matching,
        card_factory=card, equipment_presentation=None, hotkey_manager=SimpleNamespace(stop=lambda **_: None),
        minimize_window=lambda: None, restore_window=lambda: None, activate_window=lambda: None)
    page = owner.build_page()
    manager = None
    try:
        button = next(b for b in page.findChildren(QPushButton) if b.text() == matching.catalog.render("matching.title"))
        button.click()
        with patch("src.features.scanning.post_action_dialog._load_drive_shape_options", return_value=[]), \
             patch("src.features.scanning.post_action_dialog._load_set_name_options", return_value=[]), \
             patch("src.features.scanning.post_action_dialog._load_role_options", return_value=[]):
            manager = ScanPostActionDialog(parent, tmp_path / "config", Path("config"),
                user_database_path=ctx.account.user_database_path, role_suit_matching=matching)
        assert manager._main_tabs.count() == 3
        assert manager._main_tabs.tabText(2) == matching.catalog.render("matching.title")
        manager._main_tabs.setCurrentIndex(2)
        checked = []
        def inspect_popup(dialog):
            dialog.show()
            app.processEvents()
            next(b for b in dialog.findChildren(QPushButton) if b.text() == matching.catalog.render("matching.title")).click()
            matching.changed.emit(SimpleNamespace(database=ctx.account.user_database_path))
            checked.append(not dialog.findChild(QScrollArea).isEnabled())
            checked.append(any(l.text() == matching.catalog.render("matching.stale") for l in dialog.findChildren(QLabel)))
            dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Close).click()
            assert not dialog.isVisible()
            return QDialog.Rejected
        result = {"rows": [], "matching_status": "unmatched"}
        with patch.object(QDialog, "exec", inspect_popup):
            if comparison:
                show_warehouse_identification_comparison(parent, {}, {}, (result, result))
            else:
                show_warehouse_identification_dialog(parent, {}, result)
        assert len(matching.parents) == 3 and checked == [True, True]
        assert owner._matching_stale and not owner.ident_result_widget.isVisible()
    finally:
        owner.close()
        if manager is not None:
            manager.close()
        page.close()
        page.deleteLater()
        parent.deleteLater()
        app.processEvents()


def test_embedded_matching_stays_visible_after_tab_switch_and_hidden_load(app, tmp_path):
    from src.app.theme import _DialogPolishFilter
    from src.features.scanning.post_action_dialog import ScanPostActionDialog

    parent = QWidget()
    owner = RoleSuitMatchingController(context(tmp_path), catalog(tmp_path), parent)
    polish = _DialogPolishFilter(app)
    app.installEventFilter(polish)
    manager = None
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", return_value=data({"version": 1, "extra_suits": {}})), \
             patch("src.features.scanning.post_action_dialog._load_drive_shape_options", return_value=[]), \
             patch("src.features.scanning.post_action_dialog._load_set_name_options", return_value=[]), \
             patch("src.features.scanning.post_action_dialog._load_role_options", return_value=[]):
            manager = ScanPostActionDialog(parent, tmp_path / "config", Path("config"),
                user_database_path=owner.context.account.user_database_path, role_suit_matching=owner)
            manager.show()
            # 模拟副屏/移动后的管理窗口；内嵌页必须仍按父布局坐标定位。
            manager.move(900, 180)
            child = next(iter(owner._panels))
            wait_until(lambda: not child.is_running())
            panel = manager._matching_panel
            for index in (2, 0, 2, 1, 2):
                manager._main_tabs.setCurrentIndex(index)
                app.processEvents()
                if index == 2:
                    assert panel.parentWidget().rect().contains(panel.geometry())
                    assert panel.search.isVisible() and not panel.search.visibleRegion().isEmpty()
                    assert panel.role_list.count() == 2 and not panel.role_list.visibleRegion().isEmpty()
    finally:
        app.removeEventFilter(polish)
        polish.deleteLater()
        owner.close()
        wait_until(lambda: not owner.is_running() and all(not p.is_running() for p in owner._panels))
        if manager is not None:
            manager.close()
            manager.deleteLater()
        parent.deleteLater()
        app.processEvents()


def test_management_save_is_shared_atomic_and_conflict_keeps_window_open(app, tmp_path):
    from src.features.scanning.post_action_dialog import ScanPostActionDialog, load_scan_post_action_config
    ctx = context(tmp_path)
    service = MatchingSettingsService(ctx.account.user_database_path)
    parent = QWidget()
    owner = RoleSuitMatchingController(ctx, catalog(tmp_path), parent)
    managers, accepted = [], []
    def settled():
        return not owner.is_running() and all(not child.is_running() for child in owner._panels)
    def open_manager(title):
        manager = ScanPostActionDialog(parent, tmp_path / "config", Path("config"),
            user_database_path=ctx.account.user_database_path, window_title=title, role_suit_matching=owner)
        managers.append(manager)
        manager.accepted.connect(lambda: accepted.append(title))
        manager._main_tabs.setCurrentIndex(2)
        manager.show()
        wait_until(settled)
        return manager, manager._main_tabs.widget(2)
    try:
        with patch("src.ui.role_suit_matching.controller.load_matching_editor", side_effect=lambda *_: data(service.load())), \
             patch("src.ui.role_suit_matching.controller.preview_matching_role", return_value={"A": "matched"}), \
             patch("src.features.scanning.post_action_dialog._load_drive_shape_options", return_value=[]), \
             patch("src.features.scanning.post_action_dialog._load_set_name_options", return_value=[]), \
             patch("src.features.scanning.post_action_dialog._load_role_options", return_value=[]):
            first_manager, first = open_manager("仓库弃置/锁定管理")
            second_manager, second = open_manager("全量扫描管理")
            owner.open(parent)
            wait_until(settled)
            # 计算管理的内层保存同步到仓库与鉴定；随后外层取消不撤销已保存套装。
            second.role_list.itemClicked.emit(second.role_list.item(1))
            wait_until(settled)
            extra = next(second.suit_list.item(i) for i in range(second.suit_list.count())
                         if second.suit_list.item(i).data(Qt.UserRole) == "A")
            extra.setSelected(True)
            second.buttons.button(QDialogButtonBox.Save).click()
            wait_until(settled)
            assert accepted == [] and second_manager.isVisible()
            assert first.draft == owner._dialog.draft == service.load()
            assert service.load()["extra_suits"] == {"1002": ["A"]}
            second_manager._buttons.button(QDialogButtonBox.Cancel).click()
            wait_until(settled)
            assert not second_manager.isVisible() and accepted == []
            assert service.load()["extra_suits"] == {"1002": ["A"]}
            second_manager, second = open_manager("全量扫描管理")
            first.role_list.itemClicked.emit(first.role_list.item(0))
            wait_until(settled)
            assert not first_manager._buttons.isVisible()
            assert first.buttons.button(QDialogButtonBox.Save).isVisible()
            assert first.buttons.button(QDialogButtonBox.Cancel).text() == "返回"
            first.draft["extra_suits"] = {"1001": ["B"]}
            first.buttons.button(QDialogButtonBox.Cancel).click()
            assert first.stack.currentIndex() == 0 and first.draft["extra_suits"] == {"1001": ["B"]}
            first_manager.hmt_region_check.setChecked(True)
            first.role_list.itemClicked.emit(first.role_list.item(0))
            wait_until(settled)
            first.buttons.button(QDialogButtonBox.Save).click()
            wait_until(settled)
            assert accepted == [] and first_manager.isVisible()
            assert first.stack.currentIndex() == 0 and first_manager._buttons.isVisible()
            assert not first.buttons.button(QDialogButtonBox.Save).isVisible()
            assert second.draft == owner._dialog.draft == service.load()
            assert service.load()["extra_suits"] == {"1001": ["B"]}
            assert load_scan_post_action_config(None, user_database_path=ctx.account.user_database_path)["server_region"] == "default"
            from src.services.identification.matching_editor import save_matching_editor
            started, release = Event(), Event()
            def slow_save(*args, **kwargs):
                started.set()
                assert release.wait(5)
                return save_matching_editor(*args, **kwargs)
            with patch("src.ui.role_suit_matching.controller.save_matching_editor", side_effect=slow_save) as save:
                try:
                    first_manager._buttons.button(QDialogButtonBox.Save).click()
                    wait_until(started.is_set)
                    assert not first_manager._buttons.button(QDialogButtonBox.Cancel).isEnabled()
                    first_manager.reject()
                    first_manager.close()
                    assert first_manager.isVisible()
                    first_manager._buttons.button(QDialogButtonBox.Save).click()
                    assert save.call_count == 1
                finally:
                    release.set()
                    wait_until(settled)
            assert not first_manager.isVisible() and accepted == ["仓库弃置/锁定管理"]
            assert second.draft == owner._dialog.draft == service.load()
            assert load_scan_post_action_config(None, user_database_path=ctx.account.user_database_path)["server_region"] == "hmt"
            # 独立鉴定保存仍同步，关闭自身但不接受管理窗口。
            owner._dialog.draft["extra_suits"] = {"1002": ["A"]}
            owner._dialog.buttons.button(QDialogButtonBox.Save).click()
            wait_until(lambda: settled() and owner._dialog is None)
            assert second.draft == service.load()
            second.draft["extra_suits"]["1001"] = ["C"]
            third_manager, third = open_manager("再次打开仓库管理")
            third_manager.hmt_region_check.setChecked(False)
            third.draft["extra_suits"]["1001"] = ["B"]
            third_manager._main_tabs.setCurrentIndex(0)
            third_manager._buttons.button(QDialogButtonBox.Save).click()
            wait_until(settled)
            assert second.draft["extra_suits"]["1001"] == ["C"] and second.reload_button.isVisible()
            second.role_list.itemClicked.emit(second.role_list.item(0))
            wait_until(settled)
            second.buttons.button(QDialogButtonBox.Save).click()
            wait_until(settled)
            assert second.stack.currentIndex() == 1 and second_manager.isVisible()
            assert not second_manager._buttons.isVisible() and len(accepted) == 2
            assert second.draft["extra_suits"]["1001"] == ["C"]
            second.buttons.button(QDialogButtonBox.Cancel).click()
            second_manager.hmt_region_check.setChecked(True)
            second_manager._main_tabs.setCurrentIndex(1)
            second_manager._buttons.button(QDialogButtonBox.Save).click()
            wait_until(settled)
            assert second_manager.isVisible() and len(accepted) == 2
            assert second_manager._main_tabs.currentIndex() == 2
            assert service.load()["extra_suits"]["1001"] == ["B"]
            assert load_scan_post_action_config(None, user_database_path=ctx.account.user_database_path)["server_region"] == "default"
            second.reload_button.click()
            wait_until(settled)
            assert second.draft == service.load()
            second.draft["extra_suits"] = {}
            second_manager._buttons.button(QDialogButtonBox.Cancel).click()
            wait_until(settled)
            assert not second_manager.isVisible() and len(accepted) == 2
            assert service.load()["extra_suits"]["1001"] == ["B"]
            stale_manager, stale = open_manager("旧账号窗口")
            stale.draft["extra_suits"] = {}
            child = next(child for child in owner._panels if child._dialog is stale)
            ctx.generation += 1
            child.stop()
            wait_until(settled)
            assert not stale_manager.isVisible() and len(accepted) == 2
            assert service.load()["extra_suits"]["1001"] == ["B"]
    finally:
        owner.close()
        wait_until(settled)
        for manager in managers:
            manager.close()
            manager.deleteLater()
        parent.deleteLater()
        app.processEvents()


