# 验证角色资料详情 View 只发分页请求并丢弃旧角色回包。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.features.static_catalog.character_detail import CharacterDetailPanel
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)


NTE_TEST_TIER = "full"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StaticCatalogCharacterDetailUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.queries = StaticCatalogCharacterQueries(
            PROJECT_ROOT / "data" / "game_static.sqlite3"
        )
        self.service = StaticCatalogCharacterService(self.queries)
        self.panel = CharacterDetailPanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()
        self.queries.close()

    def test_tabs_request_lazy_pages_and_render_controller_results(self) -> None:
        detail = self.service.get_character_detail(1036)
        assert detail is not None
        growth_requests: list[tuple[int, int, int]] = []
        combat_requests: list[tuple[int, int, int]] = []
        self.panel.growth_page_requested.connect(
            lambda character_id, offset, limit: growth_requests.append(
                (character_id, offset, limit)
            )
        )
        self.panel.combat_page_requested.connect(
            lambda character_id, offset, limit: combat_requests.append(
                (character_id, offset, limit)
            )
        )

        self.panel.set_detail(detail)
        self.panel.tabs.setCurrentWidget(self.panel.growth_tab)
        self.panel.tabs.setCurrentWidget(self.panel.combat_tab)

        self.assertEqual((1036, 0, 40), growth_requests[-1])
        self.assertEqual((1036, 0, 100), combat_requests[-1])

        growth = self.service.list_growth(1036, limit=40)
        combat = self.service.list_combat_links(1036, limit=100)
        self.panel.set_growth_page(growth)
        self.panel.set_combat_page(combat)

        self.assertEqual(40, self.panel.growth_table.rowCount())
        self.assertEqual(min(100, combat.total), self.panel.combat_table.rowCount())
        self.assertEqual("1–40 / 86", self.panel.growth_page_label.text())
        self.assertIn("/", self.panel.combat_page_label.text())

    def test_stale_page_for_previous_character_is_ignored(self) -> None:
        first = self.service.get_character_detail(1036)
        second = self.service.get_character_detail(1075)
        assert first is not None and second is not None
        stale_page = self.service.list_growth(1036, limit=10)

        self.panel.set_detail(first)
        self.panel.set_detail(second)
        self.panel.set_growth_page(stale_page)

        self.assertEqual(0, self.panel.growth_table.rowCount())
        self.assertIn("未加载", self.panel.growth_page_label.text())


if __name__ == "__main__":
    unittest.main()
