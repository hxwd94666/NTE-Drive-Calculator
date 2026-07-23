# 校验仓库视图的官方快照投影与轻量筛选。
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "assets" / "game_ui"


class WarehouseInventoryTests(unittest.TestCase):
    def test_core_and_module_use_distinct_packaged_item_images(self):
        from src.features.inventory.warehouse import warehouse_item_view

        core = warehouse_item_view(
            {"kind": "core", "item_id": "Lakshana_orange"},
            asset_root=ASSET_ROOT,
        )
        module = warehouse_item_view(
            {"kind": "module", "item_id": "cell3_style1_1_Orange"},
            asset_root=ASSET_ROOT,
        )

        self.assertEqual(
            ASSET_ROOT / "equipment" / "core" / "Lakshana_orange.png",
            core["item_icon_path"],
        )
        self.assertEqual(
            ASSET_ROOT / "equipment" / "module" / "cell3_style1_1_Orange.png",
            module["item_icon_path"],
        )

    def test_scan_dual_thread_and_amd_controls_remain_mutually_exclusive(self):
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from src.features.allocation.execute_page import _build_scan_processing_options

        app = QApplication.instance() or QApplication([])

        class Window:
            def __init__(self):
                self._ui_preferences = {
                    "full_scan_dual_thread_processing": True,
                    "full_scan_amd_compatibility": False,
                }

            def _save_ui_preferences(self):
                pass

        window = Window()
        card = QWidget()
        QVBoxLayout(card)
        _build_scan_processing_options(window, card, lambda *_args: None)
        window.scan_amd_compat_check.setChecked(True)
        self.assertFalse(window.scan_dual_thread_check.isChecked())
        window.scan_dual_thread_check.setChecked(True)
        self.assertFalse(window.scan_amd_compat_check.isChecked())
        app.processEvents()

    def test_projection_keeps_official_state_and_stat_labels(self):
        from src.features.inventory.warehouse import warehouse_item_view

        item = warehouse_item_view(
            {
                "kind": "module",
                "uid_slot": 3,
                "uid_serial": 4,
                "quality": "Purple",
                "item_id": 1001,
                "suit_id": 10,
                "geometry": "EquipmentGeometry_Hen3",
                "level": 20,
                "max_level": 20,
                "locked": True,
                "equipped": False,
                "names": {"zh_cn": "测试驱动"},
                "suit_names": {"zh_cn": "测试套装"},
                "main_stats": [{"property_id": "AtkAdd", "value": 42, "percent": False}],
                "sub_stats": [{"property_id": "CritBase", "value": 0.075, "percent": True}],
                "discarded": True,
            }
        )

        self.assertEqual("module", item["kind"])
        self.assertEqual("紫色", item["quality_label"])
        self.assertEqual("III型驱动", item["title"])
        self.assertIn(("已弃置", "#f85149"), item["tags"])
        self.assertIn(("已锁定", "#d29922"), item["tags"])
        self.assertEqual(["攻击力  +42", "暴击率%  +7.5%"], item["stats"])
        self.assertEqual("III型驱动", item["display_name"])
        self.assertTrue(item["main_stats"][0]["main"])
        self.assertFalse(item["sub_stats"][0]["main"])
        self.assertEqual("nte-module-3-4", item["uid"])

    def test_core_uses_one_suit_name_as_its_card_heading(self):
        from src.features.inventory.warehouse import warehouse_item_view

        item = warehouse_item_view(
            {
                "kind": "core", "quality": "Gold", "uid_slot": 1, "uid_serial": 2,
                "names": {"zh_cn": "不应显示的物品名"}, "suit_names": {"zh_cn": "「卡带套装名」"},
            }
        )

        self.assertEqual("卡带套装名", item["display_name"])
        self.assertEqual("nte-core-1-2", item["uid"])

    def test_official_orange_quality_maps_to_gold_independent_of_level(self):
        from src.features.inventory.warehouse import warehouse_item_view

        item = warehouse_item_view(
            {"kind": "module", "quality": "orange", "uid_slot": 1, "uid_serial": 2, "level": 0, "max_level": 20}
        )

        self.assertEqual("gold", item["quality"])
        self.assertEqual("金色", item["quality_label"])

    def test_visual_scan_projection_marks_level_and_game_state_as_unknown(self):
        from src.features.inventory.warehouse import filter_warehouse_items, warehouse_item_view

        item = warehouse_item_view(
            {"kind": "module", "quality": "orange", "uid_slot": 1, "uid_serial": 2, "level": 20, "max_level": 20},
            source="gamepad",
        )

        self.assertFalse(item["level_known"])
        self.assertFalse(item["state_known"])
        self.assertIn(("状态未知", "#8b949e"), item["tags"])
        self.assertEqual([], filter_warehouse_items([item], status="unequipped"))
        self.assertEqual([item["uid"]], [row["uid"] for row in filter_warehouse_items([item])])

    def test_role_name_is_searchable_and_role_filter_uses_character_id(self):
        from src.features.inventory.warehouse import filter_warehouse_items, warehouse_item_view

        equipped = warehouse_item_view(
            {
                "kind": "module", "quality": "orange", "uid_slot": 1, "uid_serial": 2,
                "equipped": True, "equipped_character_id": 1051, "equipped_character_name": "「零」",
            }
        )
        other = warehouse_item_view({"kind": "core", "quality": "purple", "uid_slot": 3, "uid_serial": 4})

        self.assertEqual([equipped["uid"]], [item["uid"] for item in filter_warehouse_items([equipped, other], search="零")])
        self.assertEqual([equipped["uid"]], [item["uid"] for item in filter_warehouse_items([equipped, other], character_id=1051)])

    def test_role_avatar_name_normalizes_display_and_template_suffixes(self):
        from src.features.inventory.warehouse import _ROLE_AVATAR_ALIASES, _normalize_role_avatar_name

        self.assertEqual(_normalize_role_avatar_name("「零」"), _normalize_role_avatar_name("零（男主）"))
        self.assertEqual("主角", _ROLE_AVATAR_ALIASES["零"])

    def test_linked_type_options_follow_selected_category(self):
        from src.features.inventory.warehouse import (
            filter_warehouse_items,
            warehouse_item_type_key,
            warehouse_item_view,
            warehouse_type_options,
        )

        drive = warehouse_item_view(
            {"kind": "module", "quality": "orange", "uid_slot": 1, "uid_serial": 2, "geometry": "EquipmentGeometry_Hen3"}
        )
        core = warehouse_item_view(
            {"kind": "core", "quality": "purple", "uid_slot": 3, "uid_serial": 4, "suit_names": {"zh_cn": "静谧山庄"}}
        )

        self.assertEqual({"III型驱动", "静谧山庄"}, {label for _key, label in warehouse_type_options([drive, core])})
        self.assertEqual(["III型驱动"], [label for _key, label in warehouse_type_options([drive, core], "module")])
        self.assertEqual(["静谧山庄"], [label for _key, label in warehouse_type_options([drive, core], "core")])
        self.assertEqual([drive["uid"]], [
            item["uid"] for item in filter_warehouse_items([drive, core], item_type=warehouse_item_type_key(drive))
        ])

    def test_comparison_only_requires_matching_module_or_card_category(self):
        from src.features.inventory.warehouse import warehouse_item_compare_category, warehouse_item_view

        first_drive = warehouse_item_view({"kind": "module", "quality": "gold", "uid_slot": 1, "uid_serial": 1})
        second_drive = warehouse_item_view({"kind": "module", "quality": "gold", "uid_slot": 1, "uid_serial": 2})
        card = warehouse_item_view({"kind": "core", "quality": "gold", "uid_slot": 1, "uid_serial": 3})

        self.assertEqual(warehouse_item_compare_category(first_drive), warehouse_item_compare_category(second_drive))
        self.assertNotEqual(warehouse_item_compare_category(first_drive), warehouse_item_compare_category(card))

    def test_card_model_does_not_publish_a_hover_tooltip(self):
        from PySide6.QtCore import Qt
        from src.features.inventory.warehouse import WarehouseInventoryModel

        model = WarehouseInventoryModel()
        model.set_items([{"title": "II型驱动", "item_name": "驱动", "suit_name": "套装"}])

        self.assertIsNone(model.data(model.index(0, 0), Qt.ToolTipRole))

    def test_filter_handles_two_thousand_cards_without_widget_creation(self):
        from src.features.inventory.warehouse import filter_warehouse_items

        items = [
            {
                "kind": "module" if index % 2 == 0 else "core",
                "quality": "purple" if index % 3 == 0 else "gold",
                "equipped": index % 5 == 0,
                "locked": index % 7 == 0,
                "discarded": index % 11 == 0,
                "search_text": f"测试套装 驱动 {index}".casefold(),
            }
            for index in range(2000)
        ]

        self.assertEqual(2000, len(filter_warehouse_items(items)))
        self.assertEqual(1000, len(filter_warehouse_items(items, kind="module")))
        self.assertEqual(667, len(filter_warehouse_items(items, quality="purple")))
        self.assertEqual(1, len(filter_warehouse_items(items, search="驱动 1999")))
        self.assertEqual(182, len(filter_warehouse_items(items, status="discarded")))

    def test_local_state_edit_updates_badges_without_writing_snapshot(self):
        from src.features.inventory.warehouse import warehouse_item_with_state

        item = {
            "uid": "nte-module-1-2", "equipped": True, "locked": False,
            "discarded": False, "item_name": "驱动", "suit_name": "套装",
            "title": "驱动 · H_3", "stats": [], "equipped_character_name": "真红",
        }
        updated = warehouse_item_with_state(item, "discarded")

        self.assertFalse(item["discarded"])
        self.assertTrue(updated["discarded"])
        self.assertFalse(updated["locked"])
        self.assertIn(("已弃置", "#f85149"), updated["tags"])
        self.assertIn(("已装备", "#58a6ff"), updated["tags"])
        self.assertIn("真红", updated["search_text"])


if __name__ == "__main__":
    unittest.main()
