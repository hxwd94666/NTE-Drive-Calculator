# 测试游戏资料库菜单仅暴露玩家可用的领域入口。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from src.domain.static_catalog import (
    CatalogDomain,
    CatalogItem,
    CatalogPage,
    StaticCatalogRelease,
    StaticCatalogRequest,
)
from src.features.static_catalog.menu import (
    MENU_ENTRIES,
    CatalogMenuCard,
    StaticCatalogMenu,
)
from src.features.static_catalog.page import StaticCatalogPage


def _domains() -> tuple[CatalogDomain, ...]:
    return (
        CatalogDomain("coverage", "覆盖总览", "内部审计", 0),
        *(
            CatalogDomain(entry.domain_key, entry.title, entry.description, index + 1)
            for index, entry in enumerate(MENU_ENTRIES)
        ),
    )


class _Controller:
    PAGE_SIZE = 50

    def __init__(self) -> None:
        self.search_domains: list[str] = []

    def refresh_release(self) -> StaticCatalogRequest:
        return StaticCatalogRequest(
            release=StaticCatalogRelease(
                database_path=Path("data/game_static.sqlite3").resolve(),
                dataset_id="menu-test",
                schema_version=29,
                importer_version=34,
                built_at_utc="2026-08-30T00:00:00Z",
                source_payloads_omitted=True,
            ),
            domains=_domains(),
        )

    def search(self, *, domain_key: str, query: str, offset: int) -> CatalogPage:
        self.search_domains.append(domain_key)
        return CatalogPage(
            items=(CatalogItem(domain_key, "sample", "示例资料"),),
            total=1,
            offset=offset,
            limit=self.PAGE_SIZE,
        )

    def detail(self, *, domain_key: str, record_id: str):
        return None

    def close(self) -> None:
        return None


class StaticCatalogMenuUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.asset_root = Path("assets/game_ui").resolve()

    def test_menu_hides_all_and_coverage_and_exposes_ten_allocated_entries(self) -> None:
        menu = StaticCatalogMenu(game_ui_asset_root=self.asset_root)
        menu.set_domains(_domains())
        self.app.processEvents()

        cards = menu.findChildren(CatalogMenuCard)
        object_names = {card.objectName() for card in cards}
        self.assertEqual(len(cards), len(MENU_ENTRIES))
        self.assertNotIn("staticCatalogMenuCard_all", object_names)
        self.assertNotIn("staticCatalogMenuCard_coverage", object_names)
        self.assertIn("staticCatalogMenuCard_character", object_names)
        self.assertIn("staticCatalogMenuCard_counterfactual_models", object_names)
        menu.deleteLater()

    def test_page_starts_on_menu_and_opens_only_the_selected_domain(self) -> None:
        controller = _Controller()
        owner = QWidget()
        page = StaticCatalogPage(
            controller=cast(Any, controller),
            dialog_parent=owner,
            game_ui_asset_root=self.asset_root,
        )
        host = page.build()
        self.app.processEvents()

        self.assertEqual(page._stack.currentIndex(), 0)
        self.assertEqual(controller.search_domains, [])
        page._open_domain("character")
        self.app.processEvents()
        self.assertEqual(page._stack.currentIndex(), 1)
        self.assertEqual(controller.search_domains, ["character"])
        page._show_menu()
        self.assertEqual(page._stack.currentIndex(), 0)
        self.assertNotIn("coverage", controller.search_domains)
        self.assertNotIn("all", controller.search_domains)
        host.deleteLater()
        owner.deleteLater()


if __name__ == "__main__":
    unittest.main()
