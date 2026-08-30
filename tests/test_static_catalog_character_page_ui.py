# 验证角色图鉴使用卡片墙、正式筛选和公共养成服务接线位。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget

from src.domain.progression_stamina import (
    IdentificationLevelProjection,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)
from src.features.static_catalog.domain_pages.character_card import CharacterGalleryCard
from src.features.static_catalog.domain_pages.character_page import (
    CharacterCatalogPage,
    build_character_catalog_page,
)
from src.features.static_catalog.domain_pages.character_skills import SkillActionCard
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.storage.sqlite.static_catalog_character_queries import StaticCatalogCharacterQueries


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StaticCatalogCharacterPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.queries = StaticCatalogCharacterQueries(
            PROJECT_ROOT / "data" / "game_static.sqlite3"
        )
        self.service = StaticCatalogCharacterService(self.queries)
        self.page = build_character_catalog_page(
            service=self.service,
            game_ui_asset_root=PROJECT_ROOT / "assets" / "game_ui",
        )
        self.page.resize(1280, 900)
        self.page.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        self.queries.close()

    def test_gallery_uses_character_cards_and_no_tables(self) -> None:
        cards = self.page.findChildren(CharacterGalleryCard)

        self.assertEqual(25, len(cards))
        self.assertEqual([], self.page.findChildren(QTableWidget))
        self.assertIn("25 位角色", self.page.result_count.text())
        visible_text = "\n".join(
            label.text() for label in self.page.findChildren(QLabel)
        )
        self.assertNotIn("schema 29", visible_text)
        self.assertNotIn("importer 34", visible_text)
        self.assertNotIn("Actor 路径", visible_text)
        self.assertNotIn("资源路径", visible_text)

    def test_filters_name_id_element_and_keeps_unsupported_facets_disabled(self) -> None:
        self.page.search.setText("1036")
        self.app.processEvents()
        self.assertEqual((1036,), self.page._visible_ids)

        self.page.search.clear()
        element_button = next(
            button for button in self.page.element_group.buttons()
            if button.property("filterKey") == "咒"
        )
        element_button.click()
        self.app.processEvents()
        self.assertTrue(self.page._visible_ids)
        self.assertTrue(all(
            self.page._cards[character_id].summary.element_label == "咒"
            for character_id in self.page._visible_ids
        ))

        disabled = {
            button.text(): button
            for button in self.page.findChildren(QPushButton)
            if button.text() in {"S 级", "A 级", "常驻", "限定"}
        }
        self.assertEqual({"S 级", "A 级", "常驻", "限定"}, set(disabled))
        self.assertTrue(all(not button.isEnabled() for button in disabled.values()))
        self.assertIn("正式数据未提供", disabled["S 级"].toolTip())
        self.assertIn("正式数据未提供", disabled["常驻"].toolTip())

    def test_profile_prioritizes_aeqr_and_only_adds_formal_extra_actions(self) -> None:
        self.page.open_character(1036)
        self.app.processEvents()

        self.assertIs(self.page.stack.currentWidget(), self.page.detail_view)
        self.assertEqual("残虹", self.page.detail_view.name.text())
        action_cards = self.page.detail_view.skill_view.findChildren(SkillActionCard)
        slots = [card.action.slot for card in action_cards]
        self.assertEqual(["A", "E", "Q", "R"], slots[:4])
        self.assertIn("QTE", slots)
        self.assertIn("G", slots)
        self.assertIn("闪避反击", slots)
        self.assertNotIn("Z", slots)
        r_card = next(card for card in action_cards if card.action.slot == "R")
        self.assertFalse(r_card.action.available)
        self.assertIn("正式数据未提供", r_card.action.reason or "")

    def test_level_planner_emits_public_progression_request_without_local_result(self) -> None:
        self.page.open_character(1036)
        growth = self.page.detail_view.growth_view
        requests: list[object] = []
        self.page.progression_requested.connect(requests.append)
        growth.start_level.setCurrentIndex(4)
        growth.end_level.setCurrentIndex(69)
        growth.include_breakthroughs.setChecked(False)
        growth._request_progression()

        self.assertEqual({
            "kind": "character_level",
            "character_id": 1036,
            "from_level": 5,
            "to_level": 70,
            "include_breakthroughs": False,
        }, requests[-1])
        self.assertIn("ProgressionStaminaService 尚未接入", growth.progression_result.text())

        self.page.set_progression_result(
            target="character_level",
            result=ProgressionStaminaResult(
                status=StaminaPlanStatus.UNAVAILABLE,
                identification=IdentificationLevelProjection(60, 7, 7, False),
                deficits=(),
                runs=(),
                known_stamina=0,
                total_stamina=None,
                unresolved_item_ids=("CharacterExp",),
                gaps=("material_yield_unavailable",),
            ),
        )
        self.assertIn("缺少正式产出：CharacterExp", growth.progression_result.text())

    def test_skill_training_lists_formal_rows_and_delegates_stamina_planning(self) -> None:
        self.page.open_character(1036)
        skill_view = self.page.detail_view.skill_view
        a_card = next(
            card for card in skill_view.findChildren(SkillActionCard)
            if card.action.slot == "A"
        )
        requests: list[object] = []
        self.page.progression_requested.connect(requests.append)

        skill_view.drawer.show_action(a_card.action, "training")
        self.app.processEvents()
        calculate = next(
            button for button in skill_view.drawer.findChildren(QPushButton)
            if button.text() == "计算材料缺口与活力"
        )
        calculate.click()

        request = requests[-1]
        self.assertIsInstance(request, dict)
        assert isinstance(request, dict)
        self.assertEqual("skill", request["kind"])
        self.assertEqual(1036, request["character_id"])
        self.assertEqual("GA_Zankou_Melee", request["skill_id"])
        self.assertEqual(1, request["from_level"])
        self.assertGreater(request["to_level"], request["from_level"])
        texts = tuple(
            label.text() for label in skill_view.drawer.findChildren(QLabel)
        )
        self.assertTrue(any(
            "不做材料折算或副本/活力推算" in text for text in texts
        ))

    def test_factory_returns_public_character_page(self) -> None:
        self.assertIsInstance(self.page, CharacterCatalogPage)


if __name__ == "__main__":
    unittest.main()
