# 验证管理角色选择的正式身份、搜索和已有区域配置。
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QItemSelectionModel, QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication, QCheckBox, QDialog, QDialogButtonBox, QLabel, QMessageBox, QPushButton, QScrollArea

from src.features.scanning.post_action_dialog import (
    RoleScopeDialog,
    ScanPostActionDialog,
    _load_role_options,
    load_scan_post_action_config,
)
from src.domain.post_actions import default_post_action_config
from src.features.scanning.preserve_rule_editor import PreserveRuleEditor
from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.theme import apply_app_theme, current_theme_name


class PostActionRoleScopeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        # 离屏 Windows 使用真实中文字形度量，不让缺字方框掩盖裁切和换行。
        if os.name == "nt" and "Microsoft YaHei UI" not in QFontDatabase.families():
            fonts = Path(os.environ["WINDIR"]) / "Fonts"
            for name in ("msyh.ttc", "segoeui.ttf"):
                QFontDatabase.addApplicationFontFromData((fonts / name).read_bytes())

    def make_manager(self, directory, config=None, *, warehouse=False, user_database_path=None):
        with ExitStack() as stack:
            for name, value in (
                ("_load_drive_shape_options", [("H_3", 3), ("V_3", 3)]),
                ("_load_set_name_options", ["套装甲", "套装乙"]),
                ("_load_role_options", [(1003, "早雾", "", False), (1004, "安魂曲", "", False)]),
            ):
                stack.enter_context(patch(f"src.features.scanning.post_action_dialog.{name}", return_value=value))
            if config is not None:
                stack.enter_context(patch("src.features.scanning.post_action_dialog.load_scan_post_action_config", return_value=config))
            dialog = ScanPostActionDialog(None, Path(directory), Path(directory),
                                          show_server_region_option=not warehouse,
                                          user_database_path=user_database_path)
        self.addCleanup(dialog.close)
        return dialog

    def test_effective_type_counts_change_without_losing_inactive_choices_in_both_entries(self):
        from src.features.scanning.post_action_scope_dialogs import TypeRangeDialog

        with tempfile.TemporaryDirectory() as directory:
            for warehouse in (False, True):
                manager = self.make_manager(directory, default_post_action_config(), warehouse=warehouse)
                for module in ("discard", "lock"):
                    widgets = manager._widgets[module]
                    for scope, inactive in (("drive", "卡带"), ("tape", "驱动")):
                        widgets["type_scope"].setCurrentIndex(widgets["type_scope"].findData(scope))
                        self.assertIn(f"{inactive} 0/0", widgets["type_range_summary"].text())
                        self.assertNotIn("不生效", widgets["type_range_summary"].text())
                        self.assertIn("color:", widgets["type_range_summary"].text())
                        picker = TypeRangeDialog(manager, manager._shape_options, manager._set_options,
                                                 ["H_3"], ["套装甲"], type_scope=scope)
                        self.addCleanup(picker.close)
                        picker.shape_buttons[1][0].click()
                        picker.set_checks[1][0].click()
                        self.assertEqual((["H_3", "V_3"], ["套装甲", "套装乙"]), picker.selected_values())
                    widgets["type_scope"].setCurrentIndex(widgets["type_scope"].findData("all"))
                    self.assertIn("驱动 2/2", widgets["type_range_summary"].text())
                    self.assertIn("卡带 2/2", widgets["type_range_summary"].text())

    def test_manager_type_summary_keeps_numbers_on_one_line_beside_picker_in_three_themes(self):
        theme_before = current_theme_name()
        self.addCleanup(lambda: apply_app_theme(self.app, theme_before))
        with tempfile.TemporaryDirectory() as directory:
            for theme in ("dark", "black", "light"):
                with self.subTest(theme=theme):
                    apply_app_theme(self.app, theme)
                    manager = self.make_manager(directory, default_post_action_config())
                    manager.resize(880, 520)
                    manager.show()
                    self.app.processEvents()
                    for module in ("discard", "lock"):
                        summary = manager._widgets[module]["type_range_summary"]
                        self.assertFalse(summary.wordWrap())
                        self.assertGreaterEqual(summary.width(), summary.minimumSizeHint().width())
                        self.assertTrue(summary.visibleRegion().contains(summary.contentsRect()))
                        picker = next(w for w in summary.parentWidget().findChildren(QPushButton) if w.text() == "选择")
                        self.assertFalse(summary.geometry().intersects(picker.geometry()))
                    manager.close()

    def test_module_role_choices_and_rule_display_order_survive_save_and_reopen(self):
        config = default_post_action_config()
        config["preserve_rules"] = [
            {"name": name, "item_type": "drive", "sub_stats": ["攻击力"], "sub_match": 1, "action": action}
            for name, action in (("第一条", "keep"), ("第二条", "discard"), ("第三条", "lock"))
        ]
        with tempfile.TemporaryDirectory() as directory:
            from src.storage.sqlite.user_data_dao import UserDataDao
            database_path = Path(directory) / "account.sqlite3"
            with UserDataDao(database_path, account_id="synthetic-rules"):
                pass
            manager = self.make_manager(directory, config, user_database_path=database_path)
            with patch("src.features.scanning.post_action_dialog.RoleScopeDialog") as picker:
                picker.return_value.exec.return_value = QDialog.Accepted
                for module, selected in (("discard", [1003]), ("lock", [1004])):
                    combo = manager._widgets[module]["role_scope"]
                    combo.setCurrentIndex(combo.findData("selected"))
                    picker.return_value.selected_character_ids.return_value = selected
                    manager._widgets[module]["role_scope_button"].click()
            manager._main_tabs.setCurrentIndex(1)
            self.assertEqual("规则处理", manager._main_tabs.tabText(1))
            manager.show()
            self.app.processEvents()
            handle = next(button for button in manager.findChildren(QPushButton)
                          if button.accessibleName() == "拖动排序：第一条")
            handle.setFocus(Qt.TabFocusReason)
            QTest.keyClick(handle, Qt.Key_Down, Qt.ControlModifier)
            manager._save()
            self.assertEqual(QDialog.Accepted, manager.result())
            saved = load_scan_post_action_config(Path(directory), user_database_path=database_path)
            self.assertEqual([1003], saved["discard"]["selected_character_ids"])
            self.assertEqual([1004], saved["lock"]["selected_character_ids"])
            self.assertEqual(["第二条", "第一条", "第三条"], [rule["name"] for rule in saved["preserve_rules"]])
            self.assertFalse((Path(directory) / "scan_post_actions.json").exists())
            reopened = self.make_manager(directory, warehouse=True, user_database_path=database_path)
            self.assertEqual(saved, reopened._collect_config())

    def test_rule_long_hold_drag_and_keyboard_reorder_keep_action_identity_and_persist(self):
        config = default_post_action_config()
        config["preserve_rules"] = [
            {"name": name, "item_type": "drive", "sub_stats": ["攻击力"], "sub_match": 1, "action": action}
            for name, action in (("第一条", "keep"), ("第二条", "discard"), ("第三条", "lock"))
        ]
        with tempfile.TemporaryDirectory() as directory:
            manager = self.make_manager(directory, config)
            manager._main_tabs.setCurrentIndex(1)
            manager.resize(960, 560)
            manager.show()
            self.app.processEvents()
            def button(action, name):
                return next(w for w in manager.findChildren(QPushButton)
                            if w.isVisible() and w.accessibleName() == f"{action}：{name}")
            source = button("拖动排序", "第一条")
            target = button("拖动排序", "第三条")
            endpoint = source.mapFromGlobal(target.mapToGlobal(target.rect().bottomLeft() + QPoint(0, 4)))
            # 普通点击/短拖不排序；真实长按鼠标序列提交一次展示移动。
            QTest.mousePress(source, Qt.LeftButton)
            QTest.mouseMove(source, endpoint)
            QTest.mouseRelease(source, Qt.LeftButton, pos=endpoint)
            self.assertEqual(["第一条", "第二条", "第三条"], [r["name"] for r in manager._collect_config()["preserve_rules"]])
            QTest.mousePress(source, Qt.LeftButton)
            QTest.qWait(QApplication.startDragTime() + 20)
            QTest.mouseMove(source, endpoint)
            QTest.mouseRelease(source, Qt.LeftButton, pos=endpoint)
            self.app.processEvents()
            self.assertEqual(["第二条", "第三条", "第一条"], [r["name"] for r in manager._collect_config()["preserve_rules"]])
            moved = button("拖动排序", "第一条")
            self.assertIs(self.app.focusWidget(), moved)
            QTest.keyClick(self.app.focusWidget(), Qt.Key_Up, Qt.ControlModifier)
            self.app.processEvents()
            self.assertEqual(["第二条", "第一条", "第三条"], [r["name"] for r in manager._collect_config()["preserve_rules"]])
            with patch("src.features.scanning.post_action_dialog.PreserveRuleEditor") as editor:
                editor.return_value.exec.return_value = QDialog.Accepted
                editor.return_value.result_rule.return_value = dict(config["preserve_rules"][0], name="改名第一条")
                button("编辑规则", "第一条").click()
            self.app.processEvents()
            button("复制规则", "改名第一条").click()
            self.app.processEvents()
            enabled = next(w for w in manager.findChildren(QCheckBox)
                           if w.isVisible() and w.accessibleName() == "启用规则：第二条")
            enabled.click()
            with patch("src.features.scanning.post_action_dialog.QMessageBox.question", return_value=QMessageBox.Yes):
                button("删除规则", "第三条").click()
            manager._save()
            reopened = self.make_manager(directory)
            saved = reopened._collect_config()["preserve_rules"]
            self.assertEqual(["第二条", "改名第一条", "改名第一条 副本"], [r["name"] for r in saved])
            self.assertEqual(["discard", "keep", "keep"], [r["action"] for r in saved])
            self.assertEqual([False, True, True], [r["enabled"] for r in saved])

    def test_rule_add_action_is_in_tab_corner_and_action_labels_keep_enums(self):
        before = current_theme_name()
        self.addCleanup(lambda: apply_app_theme(self.app, before))
        config = default_post_action_config()
        config["preserve_rules"] = [
            {"name": action, "item_type": "drive", "sub_stats": ["攻击力"], "sub_match": 1, "action": action}
            for action in ("keep", "lock", "discard")
        ]
        with tempfile.TemporaryDirectory() as directory:
            for theme in ("dark", "black", "light"):
                with self.subTest(theme=theme):
                    apply_app_theme(self.app, theme)
                    manager = self.make_manager(directory, config)
                    manager.resize(880, 620)
                    manager.show()
                    self.app.processEvents()
                    button = next(w for w in manager.findChildren(QPushButton) if w.text() == "新增规则")
                    self.assertIs(button, manager._main_tabs.cornerWidget(Qt.TopRightCorner))
                    self.assertFalse(button.isVisible())
                    manager._main_tabs.setCurrentIndex(1)
                    self.app.processEvents()
                    self.assertTrue(button.isVisible())
                    self.assertLess(button.mapToGlobal(button.rect().center()).y(),
                                    manager._main_tabs.currentWidget().mapToGlobal(QPoint()).y())
                    labels = [label.text() for label in manager._main_tabs.currentWidget().findChildren(QLabel)]
                    self.assertIn("锁定", labels)
                    self.assertIn("弃置", labels)
                    self.assertFalse(any("直接" in text for text in labels))
                    with patch("src.features.scanning.post_action_dialog.PreserveRuleEditor") as editor:
                        editor.return_value.exec.return_value = QDialog.Rejected
                        button.setFocus(Qt.TabFocusReason)
                        QTest.keyClick(button, Qt.Key_Space)
                        editor.assert_called_once()
                    fit_dialog_to_available_screen(manager, available_geometry=QRect(0, 0, 660, 400))
                    self.app.processEvents()
                    self.assertTrue(button.visibleRegion().contains(button.rect()))
                    self.assertTrue(manager.rect().contains(button.mapTo(manager, button.rect().center())))
                    manager._main_tabs.setCurrentIndex(0)
                    self.assertFalse(button.isVisible())
                    manager.close()
        editor = PreserveRuleEditor(None, config["preserve_rules"][1], [("H_3", 3)], [], Path("config"))
        self.addCleanup(editor.close)
        for action, text in (("lock", "锁定"), ("discard", "弃置")):
            button = editor._action_buttons[action]
            self.assertEqual(text, button.text())
            self.assertNotIn("直接", button.parentWidget().toolTip())
            button.click()
            editor.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
            self.assertEqual(action, editor.result_rule()["action"])

    def test_rule_drag_previews_actual_insertion_and_scrolls_before_computing_drop(self):
        config = default_post_action_config()
        config["preserve_rules"] = [
            {"name": f"规则{index}", "item_type": "drive", "sub_stats": ["攻击力"], "sub_match": 1}
            for index in range(12)
        ]
        with tempfile.TemporaryDirectory() as directory:
            for direction in ("before", "after", "edge"):
                with self.subTest(direction=direction):
                    manager = self.make_manager(directory, config)
                    manager._main_tabs.setCurrentIndex(1)
                    manager.resize(960, 480)
                    manager.show()
                    self.app.processEvents()
                    rule, row = manager._rule_rows[0]
                    source = row.findChild(QPushButton)
                    target_row = manager._rule_rows[2][1]
                    if direction == "edge":
                        viewport = manager._rules_scroll.viewport()
                        point = viewport.mapToGlobal(QPoint(viewport.width() // 2, viewport.height()-2))
                    else:
                        point = target_row.mapToGlobal(QPoint(target_row.width() // 2,
                            1 if direction == "before" else target_row.height()-2))
                    QTest.mousePress(source, Qt.LeftButton)
                    QTest.qWait(QApplication.startDragTime() + 20)
                    QTest.mouseMove(source, source.mapFromGlobal(point))
                    if direction == "edge":
                        QTest.qWait(360)
                        self.assertGreater(manager._rules_scroll.verticalScrollBar().value(), 0)
                    order = manager._rule_order
                    destination = order.target_index
                    self.assertTrue(row.property("dragSource"))
                    self.assertTrue(order.indicator.isVisible())
                    self.assertTrue(order.hint.isVisible())
                    self.assertIn(str(destination+1), order.hint.text())
                    if direction != "edge":
                        self.assertEqual(1 if direction == "before" else 2, destination)
                    QTest.mouseRelease(source, Qt.LeftButton, pos=source.mapFromGlobal(point))
                    self.app.processEvents()
                    self.assertIs(manager._preserve_rules[destination], rule)
                    self.assertFalse(order.indicator.isVisible() or order.hint.isVisible())
                    self.assertTrue(all(not widget.property("dragSource") for _rule, widget in manager._rule_rows))
                    manager.close()

    def test_interrupted_rule_drag_clears_feedback_and_ignores_late_release(self):
        config = default_post_action_config()
        config["preserve_rules"] = [
            {"name": f"Rule-{index}", "item_type": "drive", "sub_stats": ["攻击力"], "sub_match": 1}
            for index in range(3)
        ]
        with tempfile.TemporaryDirectory() as directory:
            for reason in (QEvent.UngrabMouse, QEvent.WindowDeactivate, QEvent.Hide, "escape"):
                with self.subTest(reason=reason):
                    manager = self.make_manager(directory, config)
                    manager._main_tabs.setCurrentIndex(1)
                    manager.resize(960, 560)
                    manager.show()
                    self.app.processEvents()
                    handles = [row.findChild(QPushButton) for _rule, row in manager._rule_rows]
                    source, target = handles[0], handles[2]
                    endpoint = source.mapFromGlobal(target.mapToGlobal(target.rect().center()))
                    QTest.mousePress(source, Qt.LeftButton)
                    QTest.qWait(QApplication.startDragTime() + 20)
                    QTest.mouseMove(source, endpoint)
                    self.assertTrue(source._armed)
                    self.assertTrue(any(row.property("dragSource") for _rule, row in manager._rule_rows))
                    self.assertTrue(manager._rule_order.indicator.isVisible())
                    if reason == QEvent.Hide:
                        source.hide()
                    elif reason == "escape":
                        QTest.keyClick(source, Qt.Key_Escape)
                    else:
                        QApplication.sendEvent(source, QEvent(reason))
                    # 失去本次手势后，计时器、按钮按下态和落点提示须一并撤销。
                    self.assertFalse(source._hold.isActive())
                    self.assertFalse(source._armed or source._pressed or source.isDown())
                    self.assertEqual(Qt.OpenHandCursor, source.cursor().shape())
                    self.assertFalse(any(row.property("dragSource") for _rule, row in manager._rule_rows))
                    self.assertFalse(manager._rule_order.indicator.isVisible() or manager._rule_order.hint.isVisible())
                    QTest.mouseRelease(source, Qt.LeftButton, pos=endpoint)
                    self.assertEqual(["Rule-0", "Rule-1", "Rule-2"],
                                     [rule["name"] for rule in manager._collect_config()["preserve_rules"]])
                    manager.close()

    def test_drive_editor_normal_and_constrained_windows_have_one_scroll_and_reachable_stats(self):
        original_theme = current_theme_name()
        self.addCleanup(lambda: apply_app_theme(self.app, original_theme))
        for theme in ("dark", "black", "light"):
            with self.subTest(theme=theme):
                apply_app_theme(self.app, theme)
                editor = PreserveRuleEditor(None, {"item_type": "drive", "sub_match": 2}, [("H_3", 3)], [], Path("config"))
                self.addCleanup(editor.close)
                with patch("src.features.scanning.preserve_rule_editor.fit_dialog_to_available_screen",
                           side_effect=lambda dialog: fit_dialog_to_available_screen(dialog, available_geometry=QRect(0, 0, 1000, 1100))):
                    editor.show()
                    QTest.qWait(20)
                scroll = editor.findChild(QScrollArea)
                self.assertNotIn("不生效", editor.range_summary.text())
                self.assertEqual(0, scroll.verticalScrollBar().maximum())
                for widget in (editor.drive_sub_stat_list, editor.drive_required_sub_stat_list):
                    self.assertEqual(0, widget.verticalScrollBar().maximum())
                    for index in range(widget.count()):
                        rect = widget.visualItemRect(widget.item(index))
                        self.assertTrue(widget.viewport().visibleRegion().contains(rect))
                        self.assertTrue(editor.rect().contains(widget.viewport().mapTo(editor, rect.center())))
                fit_dialog_to_available_screen(editor, available_geometry=QRect(0, 0, 620, 360))
                QTest.qWait(20)
                self.assertLessEqual(editor.frameGeometry().bottom(), 360)
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                editor.drive_sub_stat_list.item(0).setSelected(True)
                for widget in (editor.drive_sub_stat_list, editor.drive_required_sub_stat_list):
                    self.assertEqual(0, widget.verticalScrollBar().maximum())
                    widget.setFocus(Qt.TabFocusReason)
                    for index in range(widget.count()):
                        widget.setCurrentItem(widget.item(index), QItemSelectionModel.NoUpdate)
                        self.app.processEvents()
                        rect = widget.visualItemRect(widget.item(index))
                        visible = QRect(widget.viewport().mapTo(scroll.viewport(), rect.topLeft()), rect.size())
                        self.assertTrue(scroll.viewport().rect().contains(visible))
                    QTest.keyClick(widget, Qt.Key_End)
                    self.app.processEvents()
                    item = widget.item(widget.count() - 1)
                    point = widget.viewport().mapTo(scroll.viewport(), widget.visualItemRect(item).center())
                    self.assertTrue(scroll.viewport().rect().contains(point))
                    QTest.keyClick(widget, Qt.Key_Space)
                    self.assertTrue(item.isSelected())
                editor._action_buttons["discard"].click()
                buttons = editor.findChild(QDialogButtonBox)
                for action in (QDialogButtonBox.Save, QDialogButtonBox.Cancel):
                    widget = buttons.button(action)
                    self.assertTrue(editor.rect().contains(widget.mapTo(editor, widget.rect().center())))
                with patch("src.features.scanning.preserve_rule_editor.QMessageBox.warning") as warning:
                    buttons.button(QDialogButtonBox.Save).click()
                    warning.assert_not_called()
                self.assertEqual(QDialog.Accepted, editor.result())
                self.assertEqual("discard", editor.result_rule()["action"])
                editor.close()

    def test_editor_mode_switch_keeps_footer_stable_and_selection_reachable_with_large_fonts(self):
        original_theme = current_theme_name()
        self.addCleanup(lambda: apply_app_theme(self.app, original_theme))
        for theme in ("dark", "black", "light"):
            for scale in (1, 1.5):
                with self.subTest(theme=theme, font_scale=scale):
                    apply_app_theme(self.app, theme)
                    if scale != 1:
                        self.app.setStyleSheet(re.sub(r"font-size:(\d+)px",
                            lambda match: f"font-size:{round(int(match[1])*scale)}px", self.app.styleSheet()))
                    editor = PreserveRuleEditor(None, {"item_type": "tape", "sub_match": 2},
                                                [("H_3", 3), ("V_3", 3)], [], Path("config"))
                    self.addCleanup(editor.close)
                    with patch("src.features.scanning.preserve_rule_editor.fit_dialog_to_available_screen",
                               side_effect=lambda dialog: fit_dialog_to_available_screen(dialog,
                                   available_geometry=QRect(0, 0, 1200, 1200))):
                        editor.show()
                        QTest.qWait(30)
                    main = editor.main_stat_list
                    main.item(main.count()-1).setSelected(True)
                    chosen = [editor.tape_sub_stat_list.item(i).text() for i in (0, 1)]
                    for index in (0, 1):
                        editor.tape_sub_stat_list.item(index).setSelected(True)
                    editor.tape_required_sub_stat_list.item(0).setSelected(True)
                    coordinates = []
                    for mode in ("tape", "drive", "tape"):
                        editor._type_buttons[mode].click()
                        QTest.qWait(30)
                        coordinates.append((editor.quality_combo.mapTo(editor._editor_scroll.widget(), QPoint()).y(),
                                            editor.range_summary.mapTo(editor._editor_scroll.widget(), QPoint()).y()))
                        _combo, active, _hint = editor._active_sub_match_widgets()
                        self.assertEqual(chosen, editor._selected_stats(active))
                        self.assertEqual([chosen[0]], editor._selected_stats(editor._active_required_sub_stat_list()))
                    self.assertEqual(coordinates[0], coordinates[1])
                    self.assertEqual(coordinates[0], coordinates[2])
                    # 小屏只缩外层可视区；每种布局都能用键盘到末项并保存正式枚举/选中值。
                    fit_dialog_to_available_screen(editor, available_geometry=QRect(0, 0, 660, 420))
                    QTest.qWait(30)
                    for mode in ("drive", "tape"):
                        editor._type_buttons[mode].click()
                        QTest.qWait(30)
                        active = editor._active_sub_match_widgets()[1]
                        active.setFocus(Qt.TabFocusReason)
                        QTest.keyClick(active, Qt.Key_End)
                        QTest.qWait(20)
                        rect = active.visualItemRect(active.currentItem())
                        center = active.viewport().mapTo(editor._editor_scroll.viewport(), rect.center())
                        self.assertTrue(editor._editor_scroll.viewport().rect().contains(center))
                        QTest.keyClick(active, Qt.Key_Space)
                        if mode == "drive":
                            self.assertEqual(0, active.verticalScrollBar().maximum())
                            self.assertEqual(0, editor.drive_required_sub_stat_list.verticalScrollBar().maximum())
                        buttons = editor.findChild(QDialogButtonBox)
                        for action in (QDialogButtonBox.Save, QDialogButtonBox.Cancel):
                            button = buttons.button(action)
                            self.assertTrue(editor.rect().contains(button.mapTo(editor, button.rect().center())))
                        with patch("src.features.scanning.preserve_rule_editor.QMessageBox.warning") as warning:
                            buttons.button(QDialogButtonBox.Save).click()
                            warning.assert_not_called()
                        result = editor.result_rule()
                        self.assertEqual(mode, result["item_type"])
                        self.assertEqual(editor._selected_stats(active), result["sub_stats"])
                        self.assertEqual([chosen[0]], result["required_sub_stats"])
                        self.assertEqual([main.item(main.count()-1).text()] if mode == "tape" else [], result["main_stats"])
                        editor.show()
                        QTest.qWait(10)
                    editor.close()

    def test_role_search_selects_the_matching_formal_identity(self):
        dialog = RoleScopeDialog(
            None,
            [(1003, "早雾", "", False), (1004, "安魂曲", "", False)],
            [],
        )
        self.addCleanup(dialog.close)
        cards_by_id = {character_id: card for card, character_id, _name in dialog.role_cards}

        dialog.search_edit.setText("zaowu")
        self.app.processEvents()

        self.assertFalse(cards_by_id[1003].isHidden())
        self.assertTrue(cards_by_id[1004].isHidden())
        cards_by_id[1003].click()
        self.assertEqual([1003], dialog.selected_character_ids())


    def test_warehouse_context_hides_region_option_without_resetting_it(self):
        config = default_post_action_config()
        config["server_region"] = "hmt"
        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch(
                    "src.features.scanning.post_action_dialog.load_scan_post_action_config",
                    return_value=config,
                ),
                patch(
                    "src.features.scanning.post_action_dialog._load_drive_shape_options",
                    return_value=[],
                ),
                patch(
                    "src.features.scanning.post_action_dialog._load_set_name_options",
                    return_value=[],
                ),
                patch(
                    "src.features.scanning.post_action_dialog._load_role_options",
                    return_value=[],
                ),
            ):
                dialog = ScanPostActionDialog(
                    None,
                    Path(temp_dir),
                    Path(temp_dir),
                    show_server_region_option=False,
                )

        self.assertTrue(dialog.hmt_region_check.isHidden())
        self.assertEqual("hmt", dialog._collect_config()["server_region"])
        dialog.close()

    def test_role_options_identify_account_custom_roles_for_portrait(self):
        class StaticDao:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_role_template_characters(self):
                return [
                    {"character_id": 1042, "name_zh": "黑羽"},
                    {"character_id": 1004, "name_zh": "安魂曲"},
                ]

        class UserDao:
            def __init__(self, *_args):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def list_custom_characters(self):
                return [{"character_id": 9001, "name_zh": "自建角色"}]

        with tempfile.TemporaryDirectory() as temp_dir:
            database_path = Path(temp_dir) / "user.sqlite3"
            database_path.touch()
            with (
                patch("src.features.scanning.post_action_dialog.StaticGameDataDao", StaticDao),
                patch("src.features.scanning.post_action_dialog.UserDataDao", UserDao),
                patch("src.features.scanning.post_action_dialog.GameUiAssetCatalog") as catalog,
            ):
                catalog.return_value.character_icon.return_value = None
                self.assertEqual(
                    [
                        (1004, "安魂曲", "", False),
                        (1042, "黑羽", "", False),
                        (9001, "自建角色", "", True),
                    ],
                    _load_role_options(database_path),
                )

if __name__ == "__main__":
    unittest.main()
