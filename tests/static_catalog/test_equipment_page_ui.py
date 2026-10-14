# 验证装备正式数值与关系、冻结库存身份、未知来源和窄窗可用性。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from src.features.static_catalog.domain_pages.equipment.catalog_model import (
    EquipmentCatalogPageController,
    ReleaseEquipmentCatalogSource,
)
from src.features.static_catalog.domain_pages.equipment.page import (
    EquipmentCatalogPage,
    EquipmentGalleryCard,
)
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.equipment_presentation import EquipmentPresentation


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
ASSET_ROOT = PROJECT_ROOT / "data" / "role_catalog" / "game_ui"


class StaticCatalogEquipmentDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ReleaseEquipmentCatalogSource(STATIC_DATABASE)
        cls.archive = cls.source.archive()

    def test_drive_and_strength_curves_use_formal_values(self) -> None:
        expected = {
            "cell2_style1_1_Blue": 336.0, "cell2_style1_1_Purple": 448.0,
            "cell2_style1_1_Orange": 560.0, "cell3_style1_1_Blue": 504.0,
            "cell3_style1_1_Purple": 672.0, "cell3_style1_1_Orange": 840.0,
            "cell4_style1_1_Blue": 672.0, "cell4_style1_1_Purple": 896.0,
            "cell4_style1_1_Orange": 1120.0,
        }
        # 面积和品质改变的是正式生命数值，不是卡片颜色或下拉顺序。
        for item_id, value in expected.items():
            hp = next(
                row for row in self.source.item_curves(item_id)
                if row.property_id == "HPMaxAdd"
            )
            self.assertEqual(value, hp.max_value, item_id)
        experience = self.source.strength_experience("Mag_orange")
        self.assertIsNotNone(experience)
        assert experience is not None
        self.assertEqual(693_000, experience.total)

    def test_suit_shapes_effects_and_graduations_close_formal_relations(self) -> None:
        suit = next(row for row in self.archive.suits if row.suit_id == "Suit10")
        self.assertEqual(
            {
                "EquipmentGeometry_Hen2", "EquipmentGeometry_Shu2",
                "EquipmentGeometry_Hen4", "EquipmentGeometry_Z3",
            },
            set(suit.required_shape_ids),
        )
        self.assertTrue(set(suit.required_shape_ids).issubset(
            row.shape_id for row in self.archive.shapes
        ))
        self.assertTrue(next(
            effect for effect in suit.effects if effect.required_count == 2
        ).modifiers)
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
        app_context = SimpleNamespace(paths=SimpleNamespace(game_ui_asset_root=ASSET_ROOT))
        self.terminology_dao = StaticGameDataDao(STATIC_DATABASE)
        self.controller = EquipmentCatalogPageController(
            ReleaseEquipmentCatalogSource(STATIC_DATABASE),
            StaticCatalogTerminologyService(self.terminology_dao),
        )
        self.page = EquipmentCatalogPage(
            controller=self.controller,
            asset_catalog=GameUiAssetCatalog(ASSET_ROOT),
            presentation=EquipmentPresentation(app_context=app_context, dialog_parent=None),
        )
        self.page.resize(1280, 900)
        self.page.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        self.terminology_dao.close()
        self.app.processEvents()

    def _text(self, widget=None, *, visible_only: bool = True) -> str:
        target = self.page if widget is None else widget
        return "\n".join(
            label.text() for label in target.findChildren(QLabel)
            if not visible_only or label.isVisible()
        )

    def _inject_mag(
        self,
        *,
        snapshot_id: int = 12,
        generation: int = 4,
        source: str = "nte_core",
    ) -> bool:
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

    def _open_owned_mag(self) -> None:
        self.page.open_equipment("Mag_orange")
        self.app.processEvents()
        # 从用户可见入口展开实例，不依赖私有 layout/body 的组织方式。
        next(
            button for button in self.page.detail.findChildren(QPushButton)
            if "我的同款" in button.text()
        ).click()
        self.app.processEvents()

    def test_frozen_inventory_projects_counts_stats_and_rejects_older_callback(self) -> None:
        self.assertTrue(self._inject_mag())
        self.page.ownership.setCurrentIndex(self.page.ownership.findData("owned"))
        self.app.processEvents()
        cards = tuple(
            card for card in self.page.findChildren(EquipmentGalleryCard)
            if card.isVisible()
        )
        self.assertEqual(("Mag_orange",), tuple(card.record.item_id for card in cards))
        self.assertIn("已拥有 1 件", self._text(cards[0]))
        self._open_owned_mag()
        text = self._text()
        self.assertIn("暴击率", text)
        self.assertIn("7.5%", text)
        self.assertIn("已锁定", text)
        self.assertIn("已装备：法帝娅", text)

        baseline = self.controller.inventory
        self.assertFalse(self._inject_mag(snapshot_id=11))
        self.assertIs(baseline, self.controller.inventory)
        self.assertTrue(self._inject_mag(snapshot_id=1, generation=5))
        self.assertEqual(1, self.controller.inventory.snapshot_id)
        # 新代次允许从较小快照重新开始，旧代次即便快照更大也不能夺回展示。
        current = self.controller.inventory
        self.assertFalse(self._inject_mag(snapshot_id=99, generation=4))
        self.assertIs(current, self.controller.inventory)

    def test_refresh_invalidation_removes_previous_account_projection(self) -> None:
        self.assertTrue(self._inject_mag())
        self._open_owned_mag()
        self.assertIn("已装备：法帝娅", self._text())

        self.page.invalidate_inventory_projection()
        self.app.processEvents()

        self.assertIsNone(self.controller.inventory)
        self.assertFalse(self.page.detail.isVisible())
        self.assertNotIn("法帝娅", self._text(self.page.detail, visible_only=False))
        mag = next(
            card for card in self.page.findChildren(EquipmentGalleryCard)
            if card.record.item_id == "Mag_orange" and card.isVisible()
        )
        self.assertIn("库存暂不可用", self._text(mag))
        self.page.ownership.setCurrentIndex(self.page.ownership.findData("owned"))
        self.app.processEvents()
        self.assertFalse(any(
            card.isVisible() for card in self.page.findChildren(EquipmentGalleryCard)
        ))
        self.assertIn("库存暂不可用", self.page.result_count.text())

    def test_visual_inventory_never_claims_lock_or_equipped_state(self) -> None:
        self.assertTrue(self._inject_mag(source="gamepad"))
        item = self.controller.inventory.items[0]
        # 视觉行即便附带原生状态字段，也只有未知状态，不提升来源能力。
        self.assertFalse(item.state_known)
        self.assertFalse(item.level_known)
        self.assertFalse(item.locked)
        self.assertEqual("", item.equipped_name)
        self._open_owned_mag()
        text = self._text()
        self.assertIn("等级未知", text)
        self.assertNotIn("已锁定", text)
        self.assertNotIn("已装备：法帝娅", text)

    def test_inventory_gallery_and_expanded_detail_fit_narrow_width(self) -> None:
        self.assertTrue(self._inject_mag())
        self.page.resize(720, 900)
        self.app.processEvents()
        scrolls = self.page.gallery.findChildren(QScrollArea)
        self.assertTrue(scrolls)
        self.assertTrue(all(
            scroll.horizontalScrollBar().maximum() == 0 for scroll in scrolls
        ))
        self._open_owned_mag()
        self.assertEqual(0, self.page.detail.horizontalScrollBar().maximum())


if __name__ == "__main__":
    unittest.main()
