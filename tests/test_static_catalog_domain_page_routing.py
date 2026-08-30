# 验证已完成领域从公共资料库菜单进入专属游戏化页面。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from src.features.static_catalog.controller import StaticCatalogController
from src.features.static_catalog.contracts import CatalogLink
from src.features.static_catalog.dependencies import (
    build_static_catalog_domain_pages,
    build_static_catalog_providers,
)
from src.features.static_catalog.domain_pages.character_page import CharacterCatalogPage
from src.features.static_catalog.domain_pages.combat_mechanics_page import (
    CombatMechanicsCatalogPage,
)
from src.features.static_catalog.domain_pages.equipment_page import EquipmentCatalogPage
from src.features.static_catalog.domain_pages.fork_page import ForkCatalogPage
from src.features.static_catalog.domain_pages.monster_page import MonsterCatalogPage
from src.features.static_catalog.page import StaticCatalogPage
from src.services.static_catalog_service import StaticCatalogService
from src.ui.equipment_presentation import EquipmentPresentation


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
ASSET_ROOT = PROJECT_ROOT / "assets" / "game_ui"


class StaticCatalogDomainPageRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_completed_entries_open_dedicated_game_pages(self) -> None:
        owner = QWidget()
        app_context = SimpleNamespace(paths=SimpleNamespace(
            asset_dir=PROJECT_ROOT / "assets",
        ))
        page_holder: dict[str, StaticCatalogPage] = {}

        def route(link: CatalogLink) -> None:
            page_holder["page"].open_catalog_link(link)

        page = StaticCatalogPage(
            controller=StaticCatalogController(StaticCatalogService(
                static_database_path=STATIC_DATABASE,
                providers=build_static_catalog_providers(STATIC_DATABASE),
            )),
            dialog_parent=owner,
            game_ui_asset_root=ASSET_ROOT,
            domain_pages=build_static_catalog_domain_pages(
                STATIC_DATABASE,
                ASSET_ROOT,
                equipment_presentation=EquipmentPresentation(
                    app_context=app_context,
                    dialog_parent=owner,
                ),
                open_catalog_link=route,
            ),
        )
        page_holder["page"] = page
        host = page.build()
        self.addCleanup(owner.deleteLater)
        self.addCleanup(host.deleteLater)
        self.addCleanup(page.close)

        expected = {
            "character": CharacterCatalogPage,
            "fork": ForkCatalogPage,
            "equipment": EquipmentCatalogPage,
            "monsters": MonsterCatalogPage,
            "combat_mechanics": CombatMechanicsCatalogPage,
        }
        for domain_key, page_type in expected.items():
            page._open_domain(domain_key)
            self.app.processEvents()
            current = page._stack.currentWidget()
            self.assertIsNotNone(current.findChild(page_type), domain_key)

        self.assertEqual(set(expected), set(page._domain_pages))

        character = page._domain_contents["character"]
        self.assertIsInstance(character, CharacterCatalogPage)

        fork = page._domain_contents["fork"]
        self.assertIsInstance(fork, ForkCatalogPage)

        page.open_catalog_link(CatalogLink(
            "character", "1036", "owner", "Effect1"
        ))
        self.assertEqual(1036, character._active_character_id)

        page.open_catalog_link(CatalogLink(
            "fork", "fork_Arachne", "owner", "1"
        ))
        self.assertIs(fork.profile_view, fork._stack.currentWidget())

        equipment = page._domain_contents["equipment"]
        self.assertIsInstance(equipment, EquipmentCatalogPage)
        page.open_catalog_link(CatalogLink(
            "equipment", "Suit1", "suit", "4"
        ))
        self.assertIs(equipment.detail, equipment.stack.currentWidget())

        monster = page._domain_contents["monsters"]
        self.assertIsInstance(monster, MonsterCatalogPage)
        self.assertFalse(hasattr(fork, "catalog_link_requested"))
        self.assertFalse(hasattr(monster, "catalog_link_requested"))


if __name__ == "__main__":
    unittest.main()
