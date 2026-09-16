# 验证角色偏好弹窗的可选弧盘和暴击率输入交互。
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class RoleSelectorPreferenceTests(unittest.TestCase):
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
        self.assertEqual({"「零」": 82.0}, selector.get_crit_rate_caps())
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
        self.assertEqual({"角色甲": 40.0}, selector.get_crit_rate_baselines())

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
        self.assertEqual({"角色甲": 12.5}, selector.get_crit_rate_baselines())

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

    def test_empty_stat_summary_can_show_not_selected(self) -> None:
        from PySide6.QtWidgets import QApplication, QLabel

        from src.features.allocation.role_selector import RoleSelector

        QApplication.instance() or QApplication([])
        selector = RoleSelector()
        label = QLabel()

        selector._refresh_selected_summary_label(
            label,
            [],
            "、",
            "未选择",
        )

        self.assertEqual("未选择", label.text())

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
        self.assertEqual([], selector._selected_substat_priority("「零」"))

        selector.tape_main_filters.pop("「零」", None)
        self.assertEqual({}, selector.get_tape_main_filters())

        selector._set_tape_main_filter("「零」", ["攻击力%"])
        selector._set_stat_priority_config("「零」", ["攻击力%"], [], False, False, "A")

        self.assertEqual({"「零」": ["攻击力%"]}, selector.get_tape_main_filters())
        self.assertEqual(["攻击力%"], selector.get_crit_priority_modes()["「零」"]["stats"])
        self.assertEqual(["攻击力%"], selector._selected_substat_priority("「零」"))


if __name__ == "__main__":
    unittest.main()
