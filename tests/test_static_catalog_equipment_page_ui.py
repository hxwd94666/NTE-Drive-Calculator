# 验证玩家化空幕与驱动图鉴、正式关系与固定库存投影。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QScrollArea, QTableView, QTableWidget

from src.features.static_catalog.dependencies import build_static_catalog_domain_pages
from src.features.static_catalog.domain_pages.equipment_page import (
    EquipmentCatalogPage,
    EquipmentGalleryCard,
    ReleaseEquipmentCatalogSource,
    build_equipment_catalog_page,
)
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.equipment_presentation import EquipmentPresentation


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
ASSET_ROOT = PROJECT_ROOT / "assets" / "game_ui"


class StaticCatalogEquipmentDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ReleaseEquipmentCatalogSource(STATIC_DATABASE)
        cls.archive = cls.source.archive()

    def test_release_archive_preserves_relations_and_all_item_icons(self) -> None:
        self.assertEqual(38, sum(row.kind == "core" for row in self.archive.equipment))
        self.assertEqual(36, sum(row.kind == "module" for row in self.archive.equipment))
        self.assertEqual(12, len(self.archive.suits))
        self.assertEqual(12, len(self.archive.shapes))
        assets = GameUiAssetCatalog(ASSET_ROOT)
        missing = tuple(
            row.item_id for row in self.archive.equipment
            if assets.inventory_item_icon(row.kind, row.item_id) is None
        )
        self.assertEqual((), missing)

    def test_drive_hp_curve_uses_official_quality_and_area_values(self) -> None:
        expected = {
            "cell2_style1_1_Blue": 336.0, "cell2_style1_1_Purple": 448.0,
            "cell2_style1_1_Orange": 560.0, "cell3_style1_1_Blue": 504.0,
            "cell3_style1_1_Purple": 672.0, "cell3_style1_1_Orange": 840.0,
            "cell4_style1_1_Blue": 672.0, "cell4_style1_1_Purple": 896.0,
            "cell4_style1_1_Orange": 1120.0,
        }
        for item_id, value in expected.items():
            hp = next(row for row in self.source.item_curves(item_id) if row.property_id == "HPMaxAdd")
            self.assertEqual(value, hp.max_value, item_id)

    def test_suit_shapes_effects_and_graduations_close_formal_relations(self) -> None:
        suit = next(row for row in self.archive.suits if row.suit_id == "Suit10")
        self.assertEqual(
            ("EquipmentGeometry_Hen2", "EquipmentGeometry_Shu2", "EquipmentGeometry_Hen4", "EquipmentGeometry_Z3"),
            suit.required_shape_ids,
        )
        self.assertTrue(next(effect for effect in suit.effects if effect.required_count == 2).modifiers)
        conditional = next(effect for effect in suit.effects if effect.required_count == 4)
        self.assertTrue(conditional.has_conditional_effect)
        self.assertIsNotNone(conditional.mechanics_link)
        links = tuple(row for row in self.archive.graduations if row.suit_id == "Suit10")
        self.assertTrue(any(row.character_name == "法帝娅" for row in links))


class StaticCatalogEquipmentPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        app_context = SimpleNamespace(paths=SimpleNamespace(asset_dir=PROJECT_ROOT / "assets"))
        self.presentation = EquipmentPresentation(app_context=app_context, dialog_parent=None)
        self.catalog_links = []
        self.terminology_dao = StaticGameDataDao(STATIC_DATABASE)
        self.page = build_equipment_catalog_page(
            database_path=STATIC_DATABASE,
            game_ui_asset_root=ASSET_ROOT,
            presentation=self.presentation,
            terminology_service=StaticCatalogTerminologyService(
                self.terminology_dao,
            ),
            open_catalog_link=self.catalog_links.append,
        )
        self.page.resize(1280, 900)
        self.page.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        self.terminology_dao.close()

    def _visible_text(self) -> str:
        return "\n".join(
            label.text() for label in self.page.findChildren(QLabel)
            if label.isVisible()
        )

    def _inject_mag(self, *, snapshot_id: int = 12, generation: int = 4, source: str = "nte_core") -> bool:
        return self.page.apply_inventory_snapshot(
            account_id="test-account",
            generation=generation,
            snapshot={
                "snapshot_id": snapshot_id,
                "source": source,
                "rows": [{
                    "item_id": "Mag_orange", "kind": "core", "quality": "Orange",
                    "suit_id": "Suit10", "level": 20, "max_level": 20,
                    "uid_slot": 3, "uid_serial": 8, "locked": True,
                    "equipped": True, "equipped_character_name": "法帝娅",
                    "main_stats": [{"property_id": "HPMaxUp", "value": 0.3}],
                    "sub_stats": [{"property_id": "CritBase", "value": 0.075}],
                }],
            },
        )

    def test_home_is_deduplicated_sorted_player_gallery_without_tables(self) -> None:
        categories = {
            button.property("categoryKey") for button in self.page.findChildren(QPushButton)
            if button.property("categoryKey")
        }
        self.assertEqual({"core", "module", "suit", "shape"}, categories)
        cards = self.page.findChildren(EquipmentGalleryCard)
        self.assertEqual(36, len(cards))
        self.assertEqual(["ORANGE", "PURPLE", "BLUE"], [card.record.quality for card in cards[:3]])
        self.assertEqual(1, len({card.record.name for card in cards[:3]}))
        self.assertFalse(self.page.findChildren(QTableView))
        self.assertFalse(self.page.findChildren(QTableWidget))
        self.assertEqual("36 项", self.page.result_count.text())
        self.assertEqual(
            ["全部品质", "S级", "A级", "B级"],
            [self.page.quality.itemText(index) for index in range(self.page.quality.count())],
        )
        card_text = "\n".join(label.text() for card in cards[:3] for label in card.findChildren(QLabel))
        self.assertIn("S级空幕", card_text)
        self.assertNotIn("金色", card_text)

    def test_internal_ids_and_resource_paths_are_not_player_visible(self) -> None:
        self._inject_mag()
        self.page.open_equipment("Mag_orange")
        self.app.processEvents()
        text = "\n".join(
            [label.text() for label in self.page.detail.findChildren(QLabel)]
            + [button.text() for button in self.page.detail.findChildren(QPushButton)]
        )
        for forbidden in (
            "item_id", "suit_id", "shape_id", "pool_id", "modify_pack",
            "property_id", "operation", "/Game/", "Buff 资源", "Mag_orange",
            "Suit10", "EquipmentGeometry", "HPMaxUp", "CritBase", "金币",
            "金色品质", "金色空幕", "金色驱动",
        ):
            self.assertNotIn(forbidden, text)
        self.assertIn("等级 / 强化曲线", text)
        self.assertIn("强化经验", text)
        self.assertIn("累计 693,000", text)
        self.assertIn("套装归属", text)
        selector = self.page.detail.findChild(QComboBox)
        self.assertIsNotNone(selector)
        self.assertFalse(selector.itemIcon(0).isNull())
        self.page.open_suit("Suit10")
        mechanics_button = next(
            button for button in self.page.detail.findChildren(QPushButton)
            if button.text() == "查看战斗机制"
        )
        mechanics_button.click()
        self.assertEqual("combat_mechanics", self.catalog_links[-1].domain_key)
        self.page.open_shape("EquipmentGeometry_Hen2")
        self.app.processEvents()
        shape_text = "\n".join(label.text() for label in self.page.detail.findChildren(QLabel))
        self.assertIn("名称暂未提供", shape_text)
        self.assertNotIn("EquipmentGeometry", shape_text)

    def test_suit_and_shape_hide_quality_and_long_sections_are_collapsed(self) -> None:
        self.page._select_category("suit")
        self.app.processEvents()
        self.assertFalse(self.page.quality.isVisible())
        self.page.open_suit("Suit10")
        self.app.processEvents()
        toggles = [
            button for button in self.page.detail.findChildren(QPushButton)
            if button.property("expandableSection")
        ]
        self.assertGreaterEqual(len(toggles), 3)
        self.assertTrue(all(button.text().startswith("展开") for button in toggles))
        self.assertTrue(all("▸" not in button.text() and "▾" not in button.text() for button in toggles))
        target = toggles[0]
        body = target.parent().body
        self.assertFalse(body.isVisible())
        target.click()
        self.app.processEvents()
        self.assertTrue(body.isVisible())
        self.assertTrue(target.text().startswith("收起"))
        self.page.show_gallery()
        self.page._select_category("shape")
        self.assertFalse(self.page.quality.isVisible())

    def test_frozen_inventory_projects_counts_stats_and_rejects_older_callback(self) -> None:
        self.assertTrue(self._inject_mag())
        self.app.processEvents()
        cards = self.page.findChildren(EquipmentGalleryCard)
        mag = next(card for card in cards if card.record.item_id == "Mag_orange" and card.isVisible())
        self.assertIn("已拥有 1 件", "\n".join(label.text() for label in mag.findChildren(QLabel)))
        self.page.open_equipment("Mag_orange")
        self.app.processEvents()
        all_text = "\n".join(label.text() for label in self.page.detail.findChildren(QLabel))
        self.assertIn("稳定仓库中有 1 件", "\n".join(
            button.text() for button in self.page.detail.findChildren(QPushButton)
        ))
        self.assertIn("暴击率", all_text)
        self.assertIn("已锁定", all_text)
        self.assertIn("已装备：法帝娅", all_text)
        self.assertFalse(self._inject_mag(snapshot_id=11))
        self.assertEqual(12, self.page._controller.inventory.snapshot_id)
        self.assertTrue(self._inject_mag(snapshot_id=1, generation=5))
        self.assertEqual(1, self.page._controller.inventory.snapshot_id)

    def test_refresh_invalidation_removes_previous_account_projection(self) -> None:
        self.assertTrue(self._inject_mag())
        self.page.open_equipment("Mag_orange")
        self.app.processEvents()
        owned_toggle = next(
            button for button in self.page.detail.findChildren(QPushButton)
            if "我的同款" in button.text()
        )
        owned_toggle.click()
        self.app.processEvents()
        self.assertIn("已装备：法帝娅", self._visible_text())

        self.page.invalidate_inventory_projection()
        self.app.processEvents()

        self.assertIsNone(self.page._controller.inventory)
        self.assertIs(self.page.stack.currentWidget(), self.page.gallery)
        self.assertNotIn("已装备：法帝娅", self._visible_text())
        self.assertNotIn(
            "法帝娅",
            "\n".join(
                label.text()
                for label in self.page.detail.findChildren(QLabel)
            ),
        )
        cards = self.page.findChildren(EquipmentGalleryCard)
        mag = next(
            card for card in cards
            if card.record.item_id == "Mag_orange" and card.isVisible()
        )
        self.assertIn(
            "库存暂不可用",
            "\n".join(label.text() for label in mag.findChildren(QLabel)),
        )
        self.page.ownership.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual("库存暂不可用", self.page.result_count.text())

    def test_visual_inventory_never_claims_lock_or_equipped_state(self) -> None:
        self.assertTrue(self._inject_mag(source="gamepad"))
        self.page.open_equipment("Mag_orange")
        self.app.processEvents()
        text = "\n".join(label.text() for label in self.page.detail.findChildren(QLabel))
        self.assertIn("名称暂未提供", text)
        self.assertNotIn("已锁定", text)
        self.assertNotIn("已装备：法帝娅", text)

    def test_owned_filter_and_narrow_layout_reflow(self) -> None:
        self._inject_mag()
        self.page.ownership.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual("1 项", self.page.result_count.text())
        self.page.ownership.setCurrentIndex(0)
        self.page.resize(720, 900)
        self.page._refresh_cards()
        positions = [self.page.grid.getItemPosition(index) for index in range(self.page.grid.count())]
        self.assertLessEqual(max(column for _row, column, _row_span, _column_span in positions), 1)
        self.assertTrue(all(
            scroll.horizontalScrollBar().maximum() == 0
            for scroll in self.page.gallery.findChildren(QScrollArea)
        ))
        self.page.open_equipment("Mag_orange")
        self.app.processEvents()
        self.assertEqual(0, self.page.detail.horizontalScrollBar().maximum())

    def test_shared_catalog_injects_the_single_public_presentation(self) -> None:
        app_context = SimpleNamespace(paths=SimpleNamespace(asset_dir=PROJECT_ROOT / "assets"))
        specs = build_static_catalog_domain_pages(
            STATIC_DATABASE,
            ASSET_ROOT,
            equipment_presentation=EquipmentPresentation(app_context=app_context, dialog_parent=None),
        )
        for spec in specs:
            self.addCleanup(spec.close)
        self.assertEqual(
            ("character", "fork", "equipment", "monsters", "combat_mechanics"),
            tuple(spec.domain_key for spec in specs),
        )
        page = next(spec for spec in specs if spec.domain_key == "equipment").build(None)
        self.addCleanup(page.deleteLater)
        self.assertIsInstance(page, EquipmentCatalogPage)

    def test_three_themes_construct_the_same_player_card_set(self) -> None:
        original = self.app.property("nte_effective_theme")
        try:
            for theme in ("dark", "black", "light"):
                self.app.setProperty("nte_effective_theme", theme)
                self.page._select_category("module")
                self.app.processEvents()
                self.assertEqual("36 项", self.page.result_count.text(), theme)
        finally:
            self.app.setProperty("nte_effective_theme", original)


if __name__ == "__main__":
    unittest.main()
