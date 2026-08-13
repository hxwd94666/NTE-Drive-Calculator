# 校验仓库视图的官方快照投影与轻量筛选。
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = PROJECT_ROOT / "assets" / "game_ui"


class WarehouseInventoryTests(unittest.TestCase):
    def test_load_log_contains_fixed_snapshot_diagnostic_counts(self):
        from src.services.warehouse_inventory_service import WarehouseInventoryService

        class Dao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def current_inventory_snapshot_id(self):
                return 12

            def inventory_snapshot_summary(self, _snapshot_id):
                return {
                    "source": "nte_core",
                    "module_count": 2,
                    "core_count": 1,
                    "equipped_count": 1,
                    "locked_count": 2,
                    "character_instance_count": 3,
                    "generation": 4,
                    "sequence": 5,
                }

            def list_inventory_items(self, _snapshot_id):
                return [
                    {"kind": "module"},
                    {"kind": "module"},
                    {"kind": "core"},
                ]

            def list_character_instance_mappings(self):
                return []

        class StaticDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def list_characters(self):
                return []

        with tempfile.NamedTemporaryFile(suffix=".sqlite3") as database:
            events: list[tuple[str, dict[str, object]]] = []
            service = WarehouseInventoryService(
                database.name,
                dao_factory=lambda _path: Dao(),
                static_dao_factory=StaticDao,
            )
            with patch(
                "src.observability.operation.log_event",
                side_effect=lambda _level, event, _message, _context, **fields: events.append(
                    (event, fields)
                ),
            ):
                service.load_current_snapshot()

        succeeded = next(
            fields for event, fields in events if event == "warehouse.load_succeeded"
        )
        self.assertEqual(3, succeeded["item_count"])
        self.assertEqual(2, succeeded["module_count"])
        self.assertEqual(1, succeeded["core_count"])
        self.assertEqual(3, succeeded["character_instance_count"])
        self.assertEqual(4, succeeded["generation"])
        self.assertEqual(5, succeeded["sequence"])

    def test_warehouse_state_progress_dialog_reports_worker_phase(self):
        from PySide6.QtWidgets import QApplication, QWidget

        from src.features.inventory.warehouse_progress import (
            close_warehouse_state_progress,
            show_warehouse_state_progress,
        )

        app = QApplication.instance() or QApplication([])
        window = QWidget()
        report = show_warehouse_state_progress(window, change_count=2)

        report("修改指令已全部提交，正在等待游戏产生新的完整背包快照…")
        window._warehouse_state_progress_timer.timeout.emit()

        dialog = window._warehouse_state_progress_dialog
        self.assertEqual("仓库状态修改进度", dialog.windowTitle())
        self.assertIn("正在等待游戏", dialog.labelText())
        self.assertEqual(0, dialog.minimum())
        self.assertEqual(0, dialog.maximum())

        close_warehouse_state_progress(window)
        self.assertIsNone(window._warehouse_state_progress_dialog)
        app.processEvents()

    def test_new_snapshot_is_deferred_while_state_change_worker_is_running(self):
        from src.features.inventory.warehouse_controller import (
            _on_warehouse_sync_state,
        )

        class Hint:
            def __init__(self):
                self.text = ""
                self.visible = False

            def setText(self, text):
                self.text = text

            def show(self):
                self.visible = True

        class Worker:
            def __init__(self, running):
                self.running = running

            def isRunning(self):
                return self.running

        class Window:
            warehouse_model = object()
            _warehouse_snapshot_id = 7
            _warehouse_pending_state_changes = {}

            def __init__(self):
                self._warehouse_state_worker = Worker(True)
                self.warehouse_hint = Hint()
                self.refresh_count = 0

            def _refresh_warehouse(self):
                self.refresh_count += 1

        window = Window()
        _on_warehouse_sync_state(
            window,
            SimpleNamespace(phase="listening", last_snapshot_id=8),
        )

        self.assertEqual(8, window._warehouse_deferred_snapshot_id)
        self.assertEqual(0, window.refresh_count)
        self.assertTrue(window.warehouse_hint.visible)

        window._warehouse_state_worker = Worker(False)
        _on_warehouse_sync_state(
            window,
            SimpleNamespace(phase="listening", last_snapshot_id=9),
        )

        self.assertEqual(9, window._warehouse_deferred_snapshot_id)
        self.assertEqual(1, window.refresh_count)

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

    def test_visual_core_uses_matching_suit_artwork_not_a_drive_fallback(self):
        from src.features.inventory.warehouse import warehouse_item_view

        core = warehouse_item_view(
            {
                "kind": "core",
                "item_id": "vision_core_316",
                "suit_id": "Suit6",
                "quality": "orange",
            },
            source="gamepad",
            asset_root=ASSET_ROOT,
        )

        self.assertEqual(
            ASSET_ROOT / "equipment" / "core" / "Cosmos_orange.png",
            core["item_icon_path"],
        )

    def test_pre_fix_visual_core_displays_the_max_level_main_value(self):
        from src.features.inventory.warehouse import warehouse_item_view

        core = warehouse_item_view(
            {
                "kind": "core",
                "item_id": "vision_core_318",
                "suit_id": "Suit6",
                "quality": "orange",
                "main_stats": [{"property_id": "CritBase", "value": 0.01, "percent": True}],
            },
            source="gamepad",
        )

        self.assertEqual("+30%", core["main_stats"][0]["value"])

    def test_scan_dual_thread_and_amd_controls_remain_mutually_exclusive(self):
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from src.features.allocation.execute_page import build_scan_processing_options

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
        build_scan_processing_options(window, card, lambda *_args: None)
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
        from src.features.inventory.warehouse import ROLE_AVATAR_ALIASES, normalize_role_avatar_name

        self.assertEqual(normalize_role_avatar_name("「零」"), normalize_role_avatar_name("零（男主）"))
        self.assertEqual("主角", ROLE_AVATAR_ALIASES["零"])

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

    def test_warehouse_rows_group_cards_by_suit_and_drives_by_official_shape_order(self):
        from src.features.inventory.warehouse import filter_warehouse_items

        rows = [
            {"uid": "card-b-1", "kind": "core", "item_type_label": "套装乙"},
            {
                "uid": "drive-3",
                "kind": "module",
                "shape_id": "EquipmentGeometry_Hen3",
                "item_type_label": "III型驱动",
            },
            {"uid": "card-a", "kind": "core", "item_type_label": "套装甲"},
            {
                "uid": "drive-2-v",
                "kind": "module",
                "shape_id": "EquipmentGeometry_Shu2",
                "item_type_label": "II型驱动",
            },
            {"uid": "card-b-2", "kind": "core", "item_type_label": "套装乙"},
            {
                "uid": "drive-2-h",
                "kind": "module",
                "shape_id": "EquipmentGeometry_Hen2",
                "item_type_label": "II型驱动",
            },
        ]

        self.assertEqual(
            ["card-b-1", "card-b-2", "card-a", "drive-2-h", "drive-2-v", "drive-3"],
            [row["uid"] for row in filter_warehouse_items(rows)],
        )

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
