# 覆盖新角色页面的官方静态库、账号指针和三套装备上下文边界。
from __future__ import annotations

import copy
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QGroupBox, QLabel, QLineEdit,
    QPushButton, QSpinBox, QTableWidget, QTabWidget, QWidget,
)

from src.app import runtime
from src.features.official_role import page
from src.features.inventory.page import _sqlite_plan_display_state
from src.services.official_role_page_service import (
    DEFAULT_THEORY_PROPERTY_IDS,
    calculate_official_role_damage_breakdown,
    calculate_official_role_equipment_gain,
    calculate_official_role_margins,
    load_official_role_detail,
    load_official_role_index,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
)
from src.services.character_weight_service import (
    ensure_account_character_weights, save_account_character_weights,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


def _snapshot(character_id: int) -> dict:
    item = {
        "uid": {"serial": 2, "slot": 1},
        "kind": "core",
        "item_id": "test_core",
        "suit_id": "Suit1",
        "geometry": None,
        "grid": None,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": True,
        "discarded": False,
        "equipped": True,
        "equipped_character_uid": {"serial": 8, "slot": 7},
        "equipped_character_id": character_id,
        "equipped_placement": None,
        "names": {"zh_cn": "测试空幕"},
        "suit_names": {"zh_cn": "测试套装"},
        "main_stats": [{
            "property_id": "CritBase", "value": 0.3, "percent": True,
            "names": {"zh_cn": "暴击率"},
        }],
        "sub_stats": [{
            "property_id": "AtkUp", "value": 0.1, "percent": True,
            "names": {"zh_cn": "攻击力"},
        }],
    }
    return {
        "complete": True,
        "generation": 1,
        "sequence": 1,
        "item_count": 1,
        "items": [item],
    }


def _snapshot_with_drive(character_id: int) -> dict:
    snapshot = copy.deepcopy(_snapshot(character_id))
    drive = copy.deepcopy(snapshot["items"][0])
    drive.update({
        "uid": {"serial": 3, "slot": 1},
        "kind": "module",
        "item_id": "test_drive",
        "geometry": "shu4",
        "names": {"zh_cn": "测试驱动"},
        "main_stats": [],
        "sub_stats": [{
            "property_id": "AtkAdd", "value": 8.0, "percent": False,
            "names": {"zh_cn": "攻击力"},
        }],
    })
    snapshot["items"].append(drive)
    snapshot["item_count"] = 2
    return snapshot


class OfficialRolePageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "user.sqlite3"
        with UserDataDao(self.database, account_id="role-page"):
            pass

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_index_and_detail_come_from_official_and_account_sqlite(self) -> None:
        index = load_official_role_index(self.database)
        character_id = int(index[0]["character_id"])
        with UserDataDao(self.database) as dao:
            dao.save_character_profile(
                character_id=character_id,
                character_level=70,
                breakthrough_stage=5,
                awakening_level=3,
                fork_id=None,
                fork_level=None,
                fork_refinement_level=None,
                selected_skill_id=None,
                skill_levels={},
            )

        detail = load_official_role_detail(self.database, character_id)

        self.assertEqual(70, detail["profile"]["character_level"])
        self.assertEqual(3, detail["profile"]["awakening_level"])
        self.assertGreater(len(detail["growth_rows"]), 0)
        self.assertGreater(len(detail["skills"]), 0)
        self.assertTrue(all("fork_id" in fork for fork in detail["forks"]))

    def test_missing_workshop_weights_seed_default_then_keep_account_edit(self) -> None:
        seeded = ensure_account_character_weights(self.database, (1075,))[1075]
        self.assertEqual("default", seeded["source_kind"])
        self.assertEqual(1.0, seeded["property_weights"]["CritBase"])

        save_account_character_weights(
            self.database, 1075, {"CritBase": 1.35, "AtkUp": 0.2},
        )
        loaded = ensure_account_character_weights(self.database, (1075,))[1075]
        self.assertEqual(1.35, loaded["property_weights"]["CritBase"])
        self.assertEqual(0.2, loaded["property_weights"]["AtkUp"])
        detail = load_official_role_detail(self.database, 1075)
        self.assertTrue(detail["property_weights_from_account"])
        self.assertEqual(1.35, detail["property_weights"]["CritBase"])

    def test_first_seed_uses_workshop_instead_of_old_calculation_snapshot(self) -> None:
        with UserDataDao(self.database) as dao:
            dao.create_optimization_profile(
                "old-calculation",
                allocation_strategy="role_priority",
                characters=[{
                    "character_id": 1075,
                    "ordinal": 0,
                    "priority_group": 0,
                    "property_weights": {"CritBase": 9.0},
                }],
            )
        seeded = ensure_account_character_weights(self.database, (1075,))[1075]
        self.assertEqual("default", seeded["source_kind"])
        self.assertEqual(1.0, seeded["property_weights"]["CritBase"])

    def test_current_saved_and_theory_contexts_are_kept_separate(self) -> None:
        with StaticGameDataDao() as static_dao:
            plan_character = next(
                character
                for character in static_dao.list_role_template_characters()
                if static_dao.get_equipment_plan(int(character["character_id"])) is not None
            )
            recommendation = static_dao.get_character_recommended_weights(
                int(plan_character["character_id"])
            )
        character_id = int(plan_character["character_id"])
        with UserDataDao(self.database) as dao:
            snapshot_id = dao.import_inventory_snapshot(_snapshot(character_id))
            dao.save_loadout_plan(
                name="saved",
                character_id=character_id,
                source_snapshot_id=snapshot_id,
                assignments=[{
                    "uid_serial": 2, "uid_slot": 1, "kind": "core",
                    "target_row": None, "target_column": None,
                }],
                status="saved",
                payload={"schema": "allocation-official-snapshot-v1"},
                is_active=True,
            )

        detail = load_official_role_detail(self.database, character_id)

        self.assertEqual(1, len(detail["equipment_contexts"]["current"]["items"]))
        self.assertEqual(1, len(detail["equipment_contexts"]["saved"]["items"]))
        self.assertTrue(detail["equipment_contexts"]["theory"]["available"])
        self.assertEqual(
            {"CritBase", "CritDamageBase", "DamageUpGeneralBase", "AtkUp"},
            set(detail["equipment_contexts"]["theory"]["property_ids"]),
        )
        expected_weights = recommendation["property_weights"]
        for property_id in detail["equipment_contexts"]["theory"]["property_ids"]:
            self.assertEqual(expected_weights[property_id], detail["theory_weights"][property_id])
        self.assertTrue(detail["theory_weights_persisted"])
        self.assertFalse(detail["equipment_contexts"]["theory"]["numeric_ready"])
        margins = calculate_official_role_margins(detail, "saved")
        self.assertIsNotNone(margins)
        self.assertGreater(margins["damage"], 0)
        breakdown = calculate_official_role_damage_breakdown(detail, "saved")
        self.assertIsNotNone(breakdown)
        element_property_id = breakdown["element_property_id"]
        supported_margins = {
            "AtkUp", "AtkAdd", "CritBase", "CritDamageBase",
            "DamageUpGeneralBase", element_property_id,
        }
        self.assertEqual(
            set(detail["property_weights"]) & supported_margins,
            {row["property_id"] for row in margins["rows"]},
        )
        self.assertTrue(all("current_value" in row for row in margins["rows"]))
        self.assertTrue(all("next_damage" in row for row in margins["rows"]))
        equipment_gain = calculate_official_role_equipment_gain(detail, "saved")
        self.assertIsNotNone(equipment_gain)
        self.assertGreater(equipment_gain["damage"], equipment_gain["baseline_damage"])
        self.assertEqual(1.0, breakdown["factors"][0]["value"])
        self.assertAlmostEqual(
            math.prod(breakdown["formula_values"]), breakdown["damage"], places=8,
        )
        factor_names = {row["name"] for row in breakdown["factors"]}
        self.assertTrue(any(name.startswith("倍率对应属性（") for name in factor_names))
        self.assertTrue({
            "技能伤害倍率", "增伤区", "暴击区", "防御区", "抗性区",
            "易伤区", "独立乘区",
        }.issubset(factor_names))

        detail["property_weights"] = {
            **detail["property_weights"],
            "AtkUp": 0.5,
            "AtkAdd": 0.5,
            "CritBase": 0.5,
            "CritDamageBase": 0.5,
            "DamageUpGeneralBase": 0.5,
            "HPMaxUp": 0.5,
            "DefAdd": 0.5,
            element_property_id: 0.5,
        }
        expanded = calculate_official_role_margins(detail, "saved")
        expanded_by_id = {row["property_id"]: row for row in expanded["rows"]}
        self.assertIn("AtkAdd", expanded_by_id)
        self.assertIn(element_property_id, expanded_by_id)
        self.assertNotIn("HPMaxUp", expanded_by_id)
        self.assertNotIn("DefAdd", expanded_by_id)
        self.assertEqual(8.0, expanded_by_id["AtkAdd"]["unit"])
        self.assertEqual(0.0125, expanded_by_id["AtkUp"]["unit"])
        self.assertEqual(0.01, expanded_by_id["CritBase"]["unit"])
        self.assertEqual(0.02, expanded_by_id["CritDamageBase"]["unit"])
        self.assertEqual(0.01, expanded_by_id["DamageUpGeneralBase"]["unit"])
        self.assertEqual(0.0125, expanded_by_id[element_property_id]["unit"])

    def test_saved_plan_replacement_uses_sqlite_inventory_and_preserves_assignment(self) -> None:
        with StaticGameDataDao() as static_dao:
            character_id = int(static_dao.list_role_template_characters()[0]["character_id"])
        snapshot = _snapshot(character_id)
        stronger = copy.deepcopy(snapshot["items"][0])
        stronger["uid"] = {"serial": 9, "slot": 1}
        stronger["sub_stats"] = [{
            "property_id": "AtkUp", "value": 0.5, "percent": True,
            "names": {"zh_cn": "攻击力"},
        }]
        snapshot["items"].append(stronger)
        snapshot["item_count"] = 2
        with UserDataDao(self.database) as dao:
            snapshot_id = dao.import_inventory_snapshot(snapshot)
            dao.save_loadout_plan(
                name="saved", character_id=character_id, source_snapshot_id=snapshot_id,
                assignments=[{"uid_serial": 2, "uid_slot": 1, "kind": "core",
                              "target_row": None, "target_column": None}],
                status="saved", payload={"schema": "allocation-official-snapshot-v1"},
                is_active=True,
            )
            occupied_plan_id = dao.save_loadout_plan(
                name="other-role", character_id=9999,
                source_snapshot_id=snapshot_id,
                assignments=[{
                    "uid_serial": 9, "uid_slot": 1, "kind": "core",
                    "target_row": None, "target_column": None,
                }],
                status="saved",
                payload={"schema": "allocation-official-snapshot-v1"},
                is_active=True,
            )
        detail = load_official_role_detail(self.database, character_id)
        target = detail["equipment_contexts"]["saved"]["items"][0]
        candidates = replacement_candidates_for_official_role(detail, "saved", target)
        self.assertEqual(1, len(candidates))
        self.assertGreater(candidates[0]["gain_percent"], 0)
        plan_id = save_official_role_replacement(
            self.database, detail, target, candidates[0]["item"],
            score=candidates[0]["damage"],
        )
        with UserDataDao(self.database) as dao:
            saved = dao.get_loadout_plan(plan_id)
            occupied = dao.get_loadout_plan(occupied_plan_id)
        self.assertTrue(saved["is_active"])
        self.assertEqual(9, saved["assignments"][0]["uid_serial"])
        self.assertTrue(saved["payload"]["last_diff"]["changed"])
        self.assertEqual(
            ["nte-core-1-9"], saved["payload"]["changed_uids"],
        )
        self.assertFalse(occupied["is_active"])
        with UserDataDao(self.database) as dao:
            active_other = next(
                plan for plan in dao.list_loadout_plans(9999) if plan["is_active"]
            )
        placeholder = active_other["assignments"][0]
        self.assertEqual(0, placeholder["uid_slot"])
        self.assertTrue(placeholder["raw_assignment"]["virtual"])
        self.assertTrue(active_other["payload"]["last_diff"]["changed"])
        with UserDataDao(self.database) as dao, StaticGameDataDao() as static_dao:
            display = _sqlite_plan_display_state(active_other, dao, static_dao)
        self.assertTrue(display["equipped_tape"]["virtual"])
        self.assertTrue(display["equipped_tape"]["is_changed"])

    def test_graduation_reference_ignores_current_growth_and_uses_signature_baseline(self) -> None:
        # 1051 has a recorded exclusive fork in the official static dataset.
        detail = load_official_role_detail(self.database, 1051)
        reference = page._graduation_benchmark_damage(detail)
        self.assertIsNotNone(reference)
        self.assertGreater(reference, 0)
        template = page._graduation_template_with_weight_substats(detail)
        self.assertIsNotNone(template)
        allowed_substats = {
            "CritBase", "CritDamageBase", "DamageUpGeneralBase", "AtkUp",
            "AtkAdd", "DefAdd", "DefUp", "HPMaxUp", "HPMaxAdd",
            "MagBase", "UnbalIntensityBase",
        }
        self.assertTrue(all(
            stat["property_id"] in allowed_substats
            for item in template["equipment"]
            for stat in item.get("sub_stats") or ()
        ))
        self.assertEqual(20, detail["graduation_template"]["drive_area"])
        self.assertEqual(
            1, detail["graduation_template"]["fork_refinement_level"]
        )
        detail["profile"].update({
            "character_level": 1, "breakthrough_stage": 0,
            "awakening_level": 0, "fork_id": None,
            "fork_level": None, "fork_refinement_level": None,
        })
        self.assertAlmostEqual(reference, page._graduation_benchmark_damage(detail))

    def test_graduation_tooltip_aggregates_substats_in_three_item_rows(self) -> None:
        tooltip = page._graduation_tooltip({
            "attributes": {
                "AtkAdd": {"filter_name_zh": "攻击力"},
                "CritBase": {"filter_name_zh": "暴击率"},
                "CritDamageBase": {"filter_name_zh": "暴击伤害"},
                "DamageUpGeneralBase": {"filter_name_zh": "伤害增加"},
            },
            "graduation_template": {
                "equipment": [{
                    "kind": "module",
                    "sub_stats": [
                        {"property_id": "AtkAdd", "value": 10.0, "percent": False},
                        {"property_id": "AtkAdd", "value": 15.0, "percent": False},
                        {"property_id": "CritBase", "value": 0.1, "percent": True},
                        {"property_id": "CritDamageBase", "value": 0.2, "percent": True},
                        {"property_id": "DamageUpGeneralBase", "value": 0.1, "percent": True},
                    ],
                }, {
                    "kind": "core",
                    "main_stats": [{"property_id": "AtkAdd", "value": 20.0, "percent": False}],
                    "sub_stats": [],
                }],
            },
        })
        self.assertIn("攻击力 25", tooltip)
        self.assertEqual(1, tooltip.count("攻击力 25"))
        self.assertIn("　　　　　伤害增加 10%", tooltip)

    def test_new_page_builds_without_old_role_json_models(self) -> None:
        host = QWidget()
        with patch.object(runtime, "USER_DATABASE_PATH", self.database, create=True):
            built = page._page_my_role(host)

        tabs = host.official_role_tabs
        self.assertGreater(tabs.count(), 0)
        self.assertEqual(1, len(built.findChildren(QLineEdit, "officialRoleSearch")))
        self.assertEqual(1, len(built.findChildren(QTabWidget, "officialRoleTabs")))
        self.assertEqual(1, len(built.findChildren(QTabWidget)))
        groups = [group.title() for group in tabs.currentWidget().widget().findChildren(QGroupBox)]
        self.assertEqual(
            [
                "词条权重",
                "边际收益（按每单位收益排序）",
                "直伤公式详情",
                "空幕加成",
                "基础加成",
                "弧盘加成",
            ],
            groups,
        )
        group = tabs.currentWidget().widget().findChild(QGroupBox, "officialRoleDriveGroup")
        selector = group.findChild(QComboBox)
        self.assertEqual(
            ["游戏当前", "已保存配装", "理论最优"],
            [selector.itemText(index) for index in range(selector.count())],
        )
        self.assertIsNone(
            tabs.currentWidget().widget().findChild(QGroupBox, "officialRoleCoreGroup")
        )
        self.assertTrue(any(
            button.text() == "查看空幕 / 驱动详情"
            for button in group.findChildren(QPushButton)
        ))
        margin_table = built.findChild(QTableWidget, "officialRoleMarginalTable")
        self.assertGreater(margin_table.rowCount(), 0)
        self.assertTrue(all(
            margin_table.item(row, 1).text() != "—"
            for row in range(margin_table.rowCount())
        ))
        self.assertIsNotNone(
            built.findChild(QTableWidget, "officialRoleDamageBonusTable")
        )
        self.assertEqual(
            8, built.findChild(QTableWidget, "officialRoleDamageFactorTable").rowCount()
        )
        formula_result = built.findChild(QLabel, "officialRoleDamageFormulaResult")
        self.assertIn("最终直伤 = 100%", formula_result.text())
        graduation = built.findChild(QLabel, "officialRoleGraduationRate")
        self.assertIsNotNone(graduation)
        self.assertIn("卡带主词条", graduation.toolTip())
        self.assertIn("毕业副词条", graduation.toolTip())
        weight_group = built.findChild(QGroupBox, "officialRoleWeightGroup")
        self.assertIsNotNone(weight_group.findChild(QLabel, "officialRoleWeightAvatar"))
        self.assertIsNone(
            built.findChild(QGroupBox, "officialRoleBaseGroup").findChild(
                QLabel, "officialRoleWeightAvatar"
            )
        )
        selected_name = tabs.tabText(tabs.currentIndex())
        host.official_role_search.setText(selected_name)
        self.assertTrue(tabs.isTabVisible(tabs.currentIndex()))
        source = Path(page.__file__).read_text(encoding="utf-8")
        self.assertNotIn("my_roles.json", source)
        self.assertNotIn("my_roles_model.json", source)

    def test_page_save_persists_the_current_pointer_editor(self) -> None:
        host = QWidget()
        with patch.object(runtime, "USER_DATABASE_PATH", self.database, create=True):
            page._page_my_role(host)
            character_id = int(
                host.official_role_tabs.tabBar().tabData(
                    host.official_role_tabs.currentIndex()
                )
            )
            editor = host._official_role_editors[character_id]
            expected = 5 if editor["awakening"].value() == 6 else 6
            editor["awakening"].setValue(expected)
            self.assertIn(character_id, host._official_role_dirty_ids)
            self.assertTrue(page._save_profiles(host, show_message=False))

        with UserDataDao(self.database) as dao:
            saved = dao.get_character_profile(character_id)
        self.assertEqual(expected, saved["awakening_level"])

    def test_role_panel_edits_and_saves_account_weights(self) -> None:
        host = QWidget()
        with patch.object(runtime, "USER_DATABASE_PATH", self.database, create=True):
            built = page._page_my_role(host)
            character_id = int(
                host.official_role_tabs.tabBar().tabData(
                    host.official_role_tabs.currentIndex()
                )
            )
            editor = host._official_role_editors[character_id]
            weight_group = built.findChild(QGroupBox, "officialRoleWeightGroup")
            spins = weight_group.findChildren(QDoubleSpinBox)
            self.assertTrue(spins)
            self.assertTrue(all(not spin.isReadOnly() for spin in spins))
            auto = next(
                button for button in built.findChildren(QPushButton)
                if button.text() == "自动设为权重"
            )
            auto.click()
            before = dict(editor["property_weights"])
            spins[0].setValue(spins[0].value() + 0.125)
            changed = {
                property_id: value
                for property_id, value in editor["property_weights"].items()
                if before.get(property_id) != value
            }
            self.assertEqual(1, len(changed))
            self.assertTrue(page._save_profiles(host, show_message=False))

        with UserDataDao(self.database) as dao:
            saved = dao.get_character_weight_preferences(character_id)
        property_id, value = next(iter(changed.items()))
        self.assertEqual(value, saved["property_weights"][property_id])

    def test_fork_level_refresh_removes_old_percentage_rows(self) -> None:
        host = QWidget()
        with patch.object(runtime, "USER_DATABASE_PATH", self.database, create=True):
            built = page._page_my_role(host)
        character_id = int(
            host.official_role_tabs.tabBar().tabData(
                host.official_role_tabs.currentIndex()
            )
        )
        detail = host._official_role_editors[character_id]["detail"]
        group = built.findChild(QGroupBox, "officialRoleForkGroup")
        combo = group.findChild(QComboBox)
        level = next(spin for spin in group.findChildren(QSpinBox) if spin.maximum() == 80)
        from src.ui.widgets import NoWheelSpinBox
        self.assertIsInstance(level, NoWheelSpinBox)
        for value in (70, 60, 80):
            level.setValue(value)
            expected_count = len(page._fork_stats(detail, combo.currentData(), value))
            shown = [
                label.text()
                for label in group.findChildren(QLabel)
                if label.text().startswith("+")
            ]
            self.assertEqual(expected_count, len(shown))

    def test_growth_change_dynamically_refreshes_margin_and_formula(self) -> None:
        host = QWidget()
        with patch.object(runtime, "USER_DATABASE_PATH", self.database, create=True):
            built = page._page_my_role(host)
        character_id = int(
            host.official_role_tabs.tabBar().tabData(
                host.official_role_tabs.currentIndex()
            )
        )
        editor = host._official_role_editors[character_id]
        damage_label = built.findChild(QLabel, "officialRoleDamageScore")
        formula_label = built.findChild(QLabel, "officialRoleDamageFormulaResult")
        before_damage = damage_label.text()
        before_formula = formula_label.text()
        growth = editor["growth"]
        growth.setValue(
            growth.minimum() if growth.value() != growth.minimum()
            else growth.maximum()
        )
        self.app.processEvents()
        self.assertNotEqual(before_damage, damage_label.text())
        refreshed_formula = built.findChild(QLabel, "officialRoleDamageFormulaResult")
        self.assertNotEqual(before_formula, refreshed_formula.text())
        context = built.findChild(QGroupBox, "officialRoleDriveGroup").findChild(QComboBox)
        context.setCurrentIndex(context.findData("theory"))
        self.app.processEvents()
        self.assertEqual("直伤评分 : --", damage_label.text())
        self.assertIsNone(built.findChild(QLabel, "officialRoleDamageFormulaResult"))
        context.setCurrentIndex(context.findData("current"))
        self.app.processEvents()
        self.assertNotEqual("直伤评分 : --", damage_label.text())
        self.assertIsNotNone(built.findChild(QLabel, "officialRoleDamageFormulaResult"))

    def test_equipment_detail_uses_old_core_and_drive_card_sections(self) -> None:
        with StaticGameDataDao() as static_dao:
            character_id = int(static_dao.list_role_template_characters()[0]["character_id"])
        with UserDataDao(self.database) as dao:
            dao.import_inventory_snapshot(_snapshot_with_drive(character_id))
        detail = load_official_role_detail(self.database, character_id)
        gain = calculate_official_role_equipment_gain(detail, "current")
        mutated = copy.deepcopy(detail)
        drive = next(
            item for item in mutated["equipment_contexts"]["current"]["items"]
            if item["kind"] == "module"
        )
        drive["main_stats"] = [{
            "property_id": "AtkAdd", "value": 9999.0, "percent": False,
        }]
        mutated_gain = calculate_official_role_equipment_gain(mutated, "current")
        self.assertIsNotNone(gain)
        self.assertIsNotNone(mutated_gain)
        self.assertAlmostEqual(gain["gain_percent"], mutated_gain["gain_percent"])
        host = QWidget()
        content = page._build_equipment_detail_content(host, detail, "current")
        groups = [group.title() for group in content.findChildren(QGroupBox)]
        self.assertEqual(["空幕属性汇总", "空幕", "驱动 (1个)"], groups)
        self.assertEqual(
            2, len(content.findChildren(QWidget, "officialRoleEquipmentCard"))
        )
        margins = [
            label.text() for label in content.findChildren(QLabel)
            if label.text().startswith("直伤收益:")
        ]
        self.assertEqual(2, len(margins))
        self.assertTrue(all(margin != "直伤收益: --" for margin in margins))
        summary_values = {
            label.text()
            for label in content.findChildren(QLabel)
            if label.text().startswith("+")
        }
        self.assertIn("+92", summary_values)
        self.assertIn("+1120", summary_values)

        legacy_calls = []
        legacy_host = QWidget()

        def equip_card(*args, **kwargs):
            legacy_calls.append((args, kwargs))
            card = QWidget()
            card.setObjectName("equipmentCard")
            return card

        legacy_host._equip_card = equip_card
        page._build_equipment_detail_content(legacy_host, detail, "current")
        self.assertEqual(2, len(legacy_calls))
        self.assertIsNone(legacy_calls[0][0][3])
        self.assertEqual("V_4", legacy_calls[1][0][0])
        self.assertEqual("V_4", legacy_calls[1][0][3])
        self.assertTrue(all(
            call[1]["card_variant"] == "inventory" for call in legacy_calls
        ))

    def test_margin_button_updates_visible_account_weight_editor(self) -> None:
        host = QWidget()
        with patch.object(runtime, "USER_DATABASE_PATH", self.database, create=True):
            built = page._page_my_role(host)
        character_id = int(
            host.official_role_tabs.tabBar().tabData(
                host.official_role_tabs.currentIndex()
            )
        )
        editor = host._official_role_editors[character_id]
        before = dict(editor["property_weights"])
        apply = next(
            button for button in built.findChildren(QPushButton)
            if button.text() == "设为权重"
        )
        with patch.object(page.QMessageBox, "information"):
            apply.click()
        self.assertTrue(editor["weights_dirty"])
        self.assertNotEqual(before, editor["property_weights"])
        self.assertIn(character_id, host._official_role_dirty_ids)


if __name__ == "__main__":
    unittest.main()
