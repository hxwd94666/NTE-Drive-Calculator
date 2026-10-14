# 验证角色偏好弹窗的可选弧盘和暴击率输入交互。
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class RoleSelectorPreferenceTests(unittest.TestCase):
    def test_soft_main_filter_is_first_option_and_round_trips_without_substat_selection(self) -> None:
        from PySide6.QtWidgets import QApplication, QDialog, QCheckBox
        from src.features.allocation.role_selector import RoleSelector

        app = QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {}}, [], tape_main_stats=["攻击力%"])
        selector.selected = ["A"]
        try:
            def choose(dialog):
                switches = dialog.findChildren(QCheckBox)
                self.assertEqual(["取消主词条硬过滤", "副词条优先级一致", "黑名单为零权重",
                                  "选取不设置等级门槛"], [switch.text() for switch in switches])
                from PySide6.QtCore import QPoint
                from PySide6.QtWidgets import QLabel
                dialog.show(); app.processEvents()
                first_y = switches[0].mapTo(dialog, QPoint()).y()
                self.assertEqual(first_y, switches[2].mapTo(dialog, QPoint()).y())
                self.assertGreater(switches[3].mapTo(dialog, QPoint()).y(), first_y)
                grade_label = next(label for label in dialog.findChildren(QLabel) if label.text() == "最低等级门槛")
                row_gap = grade_label.mapTo(dialog, QPoint()).x()-switches[3].mapTo(dialog, QPoint()).x()-switches[3].width()
                self.assertGreaterEqual(row_gap, switches[3].fontMetrics().horizontalAdvance("\u3000\u3000"))
                initial_left = grade_label.mapTo(dialog, QPoint()).x()
                dialog.resize(dialog.width()+80, dialog.height())
                app.processEvents()
                # 拉宽窗口只扩展行尾留白，等级不能脱离左侧的选项块向右漂移。
                self.assertEqual(initial_left, grade_label.mapTo(dialog, QPoint()).x())
                switches[0].setChecked(True)
                return QDialog.Accepted
            with patch.object(QDialog, "exec", choose):
                selector._manage_role_preferences("A")
            self.assertTrue(selector.get_crit_priority_modes()["A"]["soft_main_filter"])
            self.assertEqual([], selector.get_crit_priority_modes()["A"]["stats"])
            with tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / "priority.json"
                selector._write_priority_config(path)
                restored = RoleSelector()
                try:
                    restored.load_roles({"A": {}}, [], tape_main_stats=["攻击力%"])
                    restored._load_priority_config_from(path)
                    self.assertTrue(restored.get_crit_priority_modes()["A"]["soft_main_filter"])
                finally:
                    restored.close(); restored.deleteLater()
        finally:
            selector.close(); selector.deleteLater(); app.sendPostedEvents()

    def test_stat_help_explains_three_inputs_before_the_five_options(self) -> None:
        from src.features.allocation.role_selector_help import STAT_PRIORITY_HELP

        first_option = STAT_PRIORITY_HELP.index("取消主词条硬过滤")
        for label in ("卡带主词条", "卡带/驱动副词条", "副词条黑名单"):
            self.assertLess(STAT_PRIORITY_HELP.index(label), first_option)
        option_names = ("取消主词条硬过滤", "副词条优先级一致", "黑名单为零权重",
                        "选取不设置等级门槛", "最低等级门槛")
        positions = [STAT_PRIORITY_HELP.index(name) for name in option_names]
        self.assertEqual(sorted(positions), positions)
        self.assertIn("暴击率% > 暴击伤害%", STAT_PRIORITY_HELP)
        self.assertIn("最终按评分", STAT_PRIORITY_HELP)

    def test_long_stat_summaries_remain_accessible_and_save_full_order_in_limited_windows(self) -> None:
        from PySide6.QtCore import QCoreApplication, QEvent, QPoint, QRect
        from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QScrollArea, QPushButton
        from src.features.allocation.role_selector import RoleSelector
        from src.app.theme import current_style_sheet
        from src.ui.widgets import SearchableComboBox

        app = QApplication.instance() or QApplication([])
        main = ["生命值%", "攻击力%", "防御力%", "暴击率%", "暴击伤害%", "环合强度", "倾陷强度", "治疗加成",
                *[f"{element}属性异能伤害增强%" for element in ("光", "灵", "咒", "暗", "魂", "相")], "心灵伤害增强%"]
        sub = ["暴击率%", "暴击伤害%", "伤害增加%", "攻击力%", "攻击力", "防御力", "防御力%",
               "生命值%", "生命值", "环合强度", "倾陷强度"]
        for available in (QRect(0, 0, 1280, 1024), QRect(0, 0, 800, 480)):
            with self.subTest(work_area=(available.width(), available.height())):
                selector = RoleSelector(style_sheet=current_style_sheet())
                selector.load_roles({"A": {}}, [], tape_main_stats=main, drive_sub_stats=sub)
                selector.selected = ["A"]
                selector._set_tape_main_filter("A", main)
                selector._set_stat_priority_config("A", sub, sub, False, False, "A")
                created = []
                def choose(dialog):
                    created.append(dialog)
                    dialog.show(); app.processEvents()
                    scrolls = dialog.findChildren(QScrollArea)
                    self.assertEqual(1, len(scrolls))  # 三份名单共用外层滚动，不能各自截断成独立小视口。
                    scroll = scrolls[0]
                    summaries = [label for label in dialog.findChildren(QLabel)
                                 if label.toolTip() in ("、".join(main), " > ".join(sub), "、".join(sub))]
                    self.assertEqual(3, len(summaries))
                    main_row = summaries[0].parentWidget()
                    main_row.findChild(QPushButton, "btnDanger").click()
                    self.assertEqual("未选择", summaries[0].text())
                    combo = main_row.findChild(SearchableComboBox)
                    for value in main:
                        combo.setCurrentText(value)
                        main_row.findChild(QPushButton, "btnAction").click()
                    QCoreApplication.sendPostedEvents(None, QEvent.LayoutRequest)
                    app.processEvents()
                    self.assertEqual("、".join(main), summaries[0].text())
                    for label in summaries:
                        self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                        scroll.ensureWidgetVisible(label); app.processEvents()
                        self.assertTrue(scroll.viewport().rect().contains(
                            label.rect().translated(label.mapTo(scroll.viewport(), QPoint()))))
                    footer = dialog.findChild(QDialogButtonBox)
                    self.assertFalse(scroll.isAncestorOf(footer))
                    for button in footer.buttons():
                        self.assertTrue(dialog.rect().contains(button.rect().translated(button.mapTo(dialog, QPoint()))))
                    self.assertTrue(available.contains(dialog.geometry()))
                    footer.button(QDialogButtonBox.Ok).click()
                    return dialog.result()
                try:
                    with patch("src.app.window_geometry._available_geometry", return_value=available), patch.object(QDialog, "exec", choose):
                        selector._manage_role_preferences("A")
                    self.assertEqual(main, selector.get_tape_main_filters()["A"])
                    self.assertEqual(sub, selector.get_crit_priority_modes()["A"]["stats"])
                    self.assertEqual(sub, selector.get_crit_priority_modes()["A"]["blacklist"])
                finally:
                    for dialog in created:
                        dialog.close(); dialog.deleteLater()
                    selector.close(); selector.deleteLater()
                    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_latest_manual_or_weapon_action_owns_the_single_crit_cap(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "弧盘甲"}},
            [],
            weapons_db={
                "弧盘甲": {"sub_stats": {"暴击率%": 18.0}},
                "弧盘乙": {"sub_stats": {"暴击率%": 24.0}},
            },
        )
        selector.selected = ["A"]

        self.assertEqual({"A": 82.0}, selector.get_crit_rate_caps())
        selector._set_crit_rate_cap("A", 90)
        self.assertEqual("manual", selector.crit_rate_cap_sources["A"])
        self.assertEqual({"A": 90.0}, selector.get_crit_rate_caps())

        selector._set_custom_weapon("A", "弧盘乙")
        selector._set_automatic_crit_rate_cap("A", "弧盘乙")
        self.assertEqual("automatic", selector.crit_rate_cap_sources["A"])
        self.assertEqual({"A": 76.0}, selector.get_crit_rate_caps())

        selector._set_crit_rate_cap("A", 0)
        self.assertEqual("manual", selector.crit_rate_cap_sources["A"])
        self.assertEqual({}, selector.get_crit_rate_caps())

    def test_zero_manual_cap_round_trips_as_unlimited(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "priority.json"
            selector = RoleSelector()
            selector.load_roles(
                {"A": {"default_weapon": "弧盘甲"}},
                [],
                weapons_db={"弧盘甲": {"sub_stats": {"暴击率%": 18.0}}},
            )
            selector.selected = ["A"]
            selector._set_crit_rate_cap("A", 0)
            selector._write_priority_config(path)

            saved = json.loads(path.read_text(encoding="utf-8"))
            restored = RoleSelector()
            restored.load_roles(
                {"A": {"default_weapon": "弧盘甲"}},
                [],
                weapons_db={"弧盘甲": {"sub_stats": {"暴击率%": 18.0}}},
            )
            restored._load_priority_config_from(path)

        self.assertEqual({"A": 0.0}, saved["crit_rate_caps"])
        self.assertEqual({"A": "manual"}, saved["crit_rate_cap_sources"])
        self.assertEqual("manual", restored.crit_rate_cap_sources["A"])
        self.assertEqual({}, restored.get_crit_rate_caps())

    def test_legacy_automatic_cap_is_classified_when_source_is_absent(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "priority.json"
            path.write_text(
                json.dumps({"priority_list": ["A"], "crit_rate_caps": {"A": 82.0}}),
                encoding="utf-8",
            )
            selector = RoleSelector()
            selector.load_roles(
                {"A": {"default_weapon": "弧盘甲"}},
                [],
                weapons_db={"弧盘甲": {"sub_stats": {"暴击率%": 18.0}}},
            )
            selector._load_priority_config_from(path)

        self.assertEqual("automatic", selector.crit_rate_cap_sources["A"])
        self.assertEqual({"A": 82.0}, selector.get_crit_rate_caps())

    def test_optional_weapon_choice_preserves_explicit_clear(self) -> None:
        from src.features.allocation.role_selector_preferences import (
            resolve_optional_priority_choice,
        )

        self.assertEqual("", resolve_optional_priority_choice(["弧盘甲"], ""))
        self.assertEqual(
            "弧盘甲",
            resolve_optional_priority_choice(["弧盘甲"], "弧盘甲"),
        )

    def test_clearing_weapon_does_not_change_manual_crit_rate_cap(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.custom_weapons["A"] = "弧盘甲"
        selector.crit_rate_caps["A"] = 72.5

        selector._set_custom_weapon("A", "")

        self.assertNotIn("A", selector.custom_weapons)
        self.assertEqual(72.5, selector.crit_rate_caps["A"])

    def test_all_roles_start_without_default_stat_preferences(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {
                "「零」": {"character_id": 1051, "default_weapon": "专武甲"},
                "卡尼斯": {"character_id": 1071, "default_weapon": "专武乙"},
            },
            [],
            tape_main_stats=["攻击力%", "环合强度"],
            drive_sub_stats=["攻击力%", "环合强度"],
            weapons_db={
                "专武甲": {"name": "专武甲", "level_sub_stats": {"80": {"暴击率%": 18}}},
                "专武乙": {"name": "专武乙", "level_sub_stats": {"80": {}}},
            },
        )
        selector.selected = ["「零」", "卡尼斯"]

        self.assertEqual({}, selector.get_tape_main_filters())
        self.assertEqual({"「零」": "专武甲", "卡尼斯": "专武乙"}, selector.get_custom_weapons())
        self.assertEqual({"「零」": 82.0, "卡尼斯": None}, selector.get_crit_rate_caps())
        self.assertEqual({}, selector.get_crit_priority_modes())
        self.assertEqual({}, selector.get_tape_main_filter_overrides())
        self.assertEqual({}, selector.get_crit_priority_mode_overrides())
        self.assertEqual({}, selector.get_crit_rate_cap_overrides())

    def test_automatic_cap_reserves_enabled_level_ten_affinity_crit(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {
                "角色甲": {
                    "default_weapon": "暴击弧盘",
                    "likeability_crit_rate_bonus": 0.04,
                },
            },
            [],
            weapons_db={
                "暴击弧盘": {"sub_stats": {"暴击率%": 40.0}},
            },
        )
        selector.selected = ["角色甲"]

        self.assertEqual({"角色甲": 56.0}, selector.get_crit_rate_caps())
        self.assertEqual({"角色甲": 44.0}, selector.get_crit_rate_baselines())

    def test_automatic_cap_uses_synced_current_fork_instead_of_max_template(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {
                "角色甲": {
                    "default_weapon": "当前弧盘",
                    "active_fork_crit_rate_bonus": 12.5,
                    "likeability_crit_rate_bonus": 0.04,
                },
            },
            [],
            weapons_db={
                "当前弧盘": {"sub_stats": {"暴击率%": 40.0}},
            },
        )
        selector.selected = ["角色甲"]

        self.assertEqual({"角色甲": 83.5}, selector.get_crit_rate_caps())
        self.assertEqual({"角色甲": 16.5}, selector.get_crit_rate_baselines())

    def test_hand_selected_same_fork_uses_refine_one_model_and_round_trips(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        roles = {
            "A": {
                "default_weapon": "弧盘甲",
                "active_fork_crit_rate_bonus": 12.0,
                "likeability_crit_rate_bonus": 0.04,
            },
        }
        weapons = {"弧盘甲": {"sub_stats": {"暴击率%": 20.0}}}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "priority.json"
            selector = RoleSelector()
            selector.load_roles(roles, [], weapons_db=weapons)
            selector.selected = ["A"]
            self.assertEqual({"A": 84.0}, selector.get_crit_rate_caps())
            selector._set_custom_weapon("A", "弧盘甲")
            selector._set_automatic_crit_rate_cap("A", "弧盘甲")
            self.assertEqual({"A": 76.0}, selector.get_crit_rate_caps())
            self.assertEqual({"A": 24.0}, selector.get_crit_rate_baselines())
            selector._write_priority_config(path)
            saved = json.loads(path.read_text(encoding="utf-8"))

            restored = RoleSelector()
            restored.load_roles(roles, [], weapons_db=weapons)
            restored._load_priority_config_from(path)

        self.assertEqual(["A"], saved["custom_weapon_selections"])
        self.assertEqual({"A": "弧盘甲"}, restored.custom_weapons)
        self.assertEqual({"A": 76.0}, restored.get_crit_rate_caps())
        self.assertEqual({"A": 24.0}, restored.get_crit_rate_baselines())

    def test_legacy_saved_default_fork_does_not_become_hand_selection(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "priority.json"
            path.write_text(
                json.dumps({"priority_list": ["A"], "custom_weapons": {"A": "弧盘甲"}}),
                encoding="utf-8",
            )
            selector = RoleSelector()
            selector.load_roles(
                {"A": {"default_weapon": "弧盘甲", "active_fork_crit_rate_bonus": 12.0}},
                [],
                weapons_db={"弧盘甲": {"sub_stats": {"暴击率%": 20.0}}},
            )
            selector._load_priority_config_from(path)

        self.assertEqual({}, selector.custom_weapons)
        self.assertEqual({"A": 88.0}, selector.get_crit_rate_caps())

    def test_reselecting_same_fork_in_dialog_records_explicit_choice(self) -> None:
        from PySide6.QtWidgets import QApplication, QDialog

        from src.features.allocation.role_selector import RoleSelector
        from src.ui.widgets import SearchableComboBox

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "弧盘甲", "active_fork_crit_rate_bonus": 12.0}},
            [],
            weapons_db={"弧盘甲": {"sub_stats": {"暴击率%": 20.0}}},
        )
        selector.selected = ["A"]

        def reselect(dialog):
            weapon_combo = dialog.findChildren(SearchableComboBox)[1]
            self.assertEqual("弧盘甲", weapon_combo.currentText())
            weapon_combo.activated.emit(weapon_combo.currentIndex())
            return QDialog.Accepted

        with patch.object(QDialog, "exec", reselect):
            selector._manage_role_preferences("A")

        self.assertEqual({"A": "弧盘甲"}, selector.custom_weapons)
        self.assertEqual({"A": 80.0}, selector.get_crit_rate_caps())

    def test_confirmed_zero_fork_and_affinity_keep_automatic_hundred(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "无暴击弧盘", "active_fork_crit_rate_bonus": 0.0}},
            [],
            weapons_db={"无暴击弧盘": {"sub_stats": {"攻击力%": 12.0}}},
        )
        selector.selected = ["A"]

        self.assertEqual({"A": 100.0}, selector.get_crit_rate_caps())
        self.assertEqual({"A": 0.0}, selector.get_crit_rate_baselines())
        selector._set_crit_rate_cap("A", 0)
        self.assertEqual({}, selector.get_crit_rate_caps())

    def test_fork_without_permanent_evidence_is_not_confirmed_zero(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "待审弧盘", "active_fork_crit_rate_bonus": None}},
            [],
            weapons_db={
                "待审弧盘": {
                    "permanent_properties_known": False,
                    "sub_stats": {"攻击力%": 12.0},
                },
            },
        )
        selector.selected = ["A"]

        self.assertEqual({"A": None}, selector.get_crit_rate_caps())
        self.assertEqual({}, selector.get_crit_rate_baselines())

        selector._set_crit_rate_cap("A", 0)
        self.assertEqual({}, selector.get_crit_rate_caps())

    def test_unknown_account_refinement_does_not_fall_back_to_fork_template(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({
            "A": {
                "default_weapon": "当前弧盘",
                "active_fork_crit_rate_bonus": None,
                "active_fork_crit_source_resolved": True,
            },
        }, [], weapons_db={
            "当前弧盘": {
                "permanent_properties_known": True,
                "sub_stats": {"暴击率%": 20.0},
            },
        })
        selector.selected = ["A"]

        self.assertEqual({"A": None}, selector.get_crit_rate_caps())
        self.assertEqual({}, selector.get_crit_rate_baselines())
        selector._set_custom_weapon("A", "当前弧盘")
        self.assertEqual({"A": 80.0}, selector.get_crit_rate_caps())

    def test_custom_character_without_fork_has_confirmed_zero_contribution(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({"A": {"is_custom": True, "default_weapon": ""}}, [])
        selector.selected = ["A"]

        self.assertEqual({"A": 100.0}, selector.get_crit_rate_caps())
        self.assertEqual({"A": 0.0}, selector.get_crit_rate_baselines())

    def test_official_role_explicitly_without_fork_has_confirmed_zero(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles({
            "A": {"default_weapon": "", "active_fork_crit_rate_bonus": 0.0},
        }, [])
        selector.selected = ["A"]

        self.assertEqual({"A": 100.0}, selector.get_crit_rate_caps())
        self.assertEqual({"A": 0.0}, selector.get_crit_rate_baselines())

    def test_automatic_zero_is_distinct_from_manual_zero(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "弧盘", "active_fork_crit_rate_bonus": 100.0}},
            [],
            weapons_db={"弧盘": {"sub_stats": {"暴击率%": 100.0}}},
        )
        selector.selected = ["A"]

        self.assertEqual({"A": 0.0}, selector.get_crit_rate_caps())
        selector._set_crit_rate_cap("A", 0)
        self.assertEqual({}, selector.get_crit_rate_caps())

    def test_selected_fork_uses_maximum_available_level_not_hardcoded_eighty(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"A": {"default_weapon": "旧弧盘"}}, [],
            weapons_db={
                "旧弧盘": {"sub_stats": {"暴击率%": 10.0}},
                "新弧盘": {
                    "level_sub_stats": {
                        "20": {"暴击率%": 5.0}, "70": {"暴击率%": 20.0},
                    },
                },
            },
        )
        selector.selected = ["A"]
        selector._set_custom_weapon("A", "新弧盘")

        self.assertEqual({"A": 80.0}, selector.get_crit_rate_caps())
        self.assertEqual({"A": 20.0}, selector.get_crit_rate_baselines())

    def test_legacy_default_mag_character_ids_do_not_inject_preferences(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {
                "A": {"character_id": 1051},
                "B": {"character_id": 2},
                "C": {"character_id": 3},
            },
            [],
            tape_main_stats=["攻击力%", "环合强度"],
            drive_sub_stats=["攻击力%", "环合强度"],
            default_mag_character_ids={1051, 2},
        )
        selector.selected = ["A", "B", "C"]

        self.assertEqual({}, selector.get_tape_main_filters())
        self.assertEqual({}, selector.get_crit_priority_modes())

    def test_protagonist_starts_empty_and_accepts_player_values(self) -> None:
        from PySide6.QtWidgets import QApplication

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        selector.load_roles(
            {"「零」": {"character_id": 1051}},
            [],
            tape_main_stats=["攻击力%", "环合强度"],
            drive_sub_stats=["攻击力%", "环合强度"],
        )
        selector.selected = ["「零」"]

        self.assertEqual({}, selector.get_tape_main_filters())
        self.assertEqual({}, selector.get_crit_priority_modes())

        selector._set_tape_main_filter("「零」", [])
        selector._set_stat_priority_config("「零」", [], [], False, False, "A")

        self.assertEqual({}, selector.get_tape_main_filters())
        self.assertEqual({}, selector.get_crit_priority_modes())

        selector.tape_main_filters.pop("「零」", None)
        self.assertEqual({}, selector.get_tape_main_filters())

        selector._set_tape_main_filter("「零」", ["攻击力%"])
        selector._set_stat_priority_config("「零」", ["攻击力%"], [], False, False, "A")

        self.assertEqual({"「零」": ["攻击力%"]}, selector.get_tape_main_filters())
        self.assertEqual(["攻击力%"], selector.get_crit_priority_modes()["「零」"]["stats"])


if __name__ == "__main__":
    unittest.main()
