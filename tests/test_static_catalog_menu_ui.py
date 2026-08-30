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
from src.features.static_catalog.page import (
    StaticCatalogDomainPageSpec,
    StaticCatalogPage,
)
from src.services.static_catalog_mechanics_service import CatalogLink


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
                schema_version=30,
                importer_version=35,
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

    def test_menu_exposes_only_the_five_player_facing_entries(self) -> None:
        menu = StaticCatalogMenu(game_ui_asset_root=self.asset_root)
        menu.set_domains(_domains())
        self.app.processEvents()

        cards = menu.findChildren(CatalogMenuCard)
        object_names = {card.objectName() for card in cards}
        self.assertEqual(len(cards), len(MENU_ENTRIES))
        self.assertNotIn("staticCatalogMenuCard_all", object_names)
        self.assertNotIn("staticCatalogMenuCard_coverage", object_names)
        self.assertNotIn("staticCatalogMenuCard_assets", object_names)
        self.assertNotIn("staticCatalogMenuCard_sources", object_names)
        self.assertIn("staticCatalogMenuCard_character", object_names)
        self.assertIn("staticCatalogMenuCard_combat_mechanics", object_names)
        menu.deleteLater()

    def test_menu_reflows_to_one_column_without_a_narrow_horizontal_grid(self) -> None:
        menu = StaticCatalogMenu(game_ui_asset_root=self.asset_root)
        menu.resize(600, 800)
        menu.set_domains(_domains())
        menu.show()
        self.app.processEvents()

        self.assertEqual(1, menu._columns)
        for grid, cards in menu._group_cards:
            for index, _card in enumerate(cards):
                _row, column, _row_span, _column_span = grid.getItemPosition(index)
                self.assertEqual(0, column)

        menu.resize(900, 800)
        self.app.processEvents()
        self.assertEqual(2, menu._columns)
        two_card_grid, _cards = menu._group_cards[0]
        self.assertEqual(0, two_card_grid.getItemPosition(0)[1])
        self.assertEqual(1, two_card_grid.getItemPosition(1)[1])
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

    def test_page_lazily_opens_registered_domain_page_and_closes_its_owner(self) -> None:
        controller = _Controller()
        owner = QWidget()
        built: list[QWidget] = []
        closed: list[bool] = []

        def build_character(parent: QWidget) -> QWidget:
            widget = QWidget(parent)
            widget.setObjectName("registeredCharacterPage")
            built.append(widget)
            return widget

        page = StaticCatalogPage(
            controller=cast(Any, controller),
            dialog_parent=owner,
            game_ui_asset_root=self.asset_root,
            domain_pages=(StaticCatalogDomainPageSpec(
                domain_key="character",
                title="角色图鉴",
                build=build_character,
                close=lambda: closed.append(True),
            ),),
        )
        page.build()
        self.assertEqual([], built)

        page._open_domain("character")
        self.app.processEvents()
        self.assertEqual(1, len(built))
        self.assertIs(page._stack.currentWidget(), page._domain_pages["character"])
        self.assertEqual([], controller.search_domains)

        page._show_menu()
        page._open_domain("character")
        self.assertEqual(1, len(built))
        page.close()
        page.close()
        self.assertEqual([True], closed)
        owner.deleteLater()

    def test_registered_domain_is_available_without_legacy_provider_and_routes_links(self) -> None:
        class ProviderOnlyController(_Controller):
            def refresh_release(self) -> StaticCatalogRequest:
                request = super().refresh_release()
                return StaticCatalogRequest(
                    release=request.release,
                    domains=(CatalogDomain("coverage", "覆盖总览", "", 0),),
                )

        class MechanicsPage(QWidget):
            def __init__(self, parent: QWidget) -> None:
                super().__init__(parent)
                self.opened: list[str] = []

            def open_record(self, record_id: str) -> None:
                self.opened.append(record_id)

        owner = QWidget()
        refreshed: list[bool] = []
        mechanics: list[MechanicsPage] = []

        def build_mechanics(parent: QWidget) -> QWidget:
            widget = MechanicsPage(parent)
            mechanics.append(widget)
            return widget

        page = StaticCatalogPage(
            controller=cast(Any, ProviderOnlyController()),
            dialog_parent=owner,
            game_ui_asset_root=self.asset_root,
            domain_pages=(StaticCatalogDomainPageSpec(
                domain_key="combat_mechanics",
                title="战斗机制图鉴",
                build=build_mechanics,
                close=lambda: None,
                refresh=lambda: refreshed.append(True),
            ),),
        )
        page.build()

        self.assertIn("combat_mechanics", page._domain_labels)
        self.assertEqual([True], refreshed)
        page.open_catalog_link(CatalogLink(
            "combat_mechanics", "formula|defense", "formula",
        ))
        self.assertEqual(["formula|defense"], mechanics[0].opened)
        page.close()
        owner.deleteLater()

    def test_dedicated_page_build_failure_returns_to_menu(self) -> None:
        controller = _Controller()
        owner = QWidget()
        errors: list[tuple[str, str]] = []

        def fail_build(_parent: QWidget) -> QWidget:
            raise RuntimeError("broken dedicated page")

        page = StaticCatalogPage(
            controller=cast(Any, controller),
            dialog_parent=owner,
            game_ui_asset_root=self.asset_root,
            domain_pages=(StaticCatalogDomainPageSpec(
                domain_key="character",
                title="角色图鉴",
                build=fail_build,
                close=lambda: None,
            ),),
        )
        page._show_error = lambda title, error: errors.append((title, str(error)))
        page.build()

        page._open_domain("character")
        self.app.processEvents()

        self.assertEqual(0, page._stack.currentIndex())
        self.assertNotIn("character", page._domain_pages)
        self.assertEqual([("无法打开角色图鉴", "broken dedicated page")], errors)
        page.close()
        owner.deleteLater()

    def test_close_attempts_every_owner_when_earlier_close_fails(self) -> None:
        class FailingController(_Controller):
            def close(self) -> None:
                raise RuntimeError("controller close failed")

        closed: list[str] = []

        def fail_first() -> None:
            closed.append("first")
            raise RuntimeError("first spec close failed")

        owner = QWidget()
        page = StaticCatalogPage(
            controller=cast(Any, FailingController()),
            dialog_parent=owner,
            game_ui_asset_root=self.asset_root,
            domain_pages=(
                StaticCatalogDomainPageSpec(
                    "character", "角色图鉴", QWidget, fail_first,
                ),
                StaticCatalogDomainPageSpec(
                    "fork", "弧盘图鉴", QWidget,
                    lambda: closed.append("second"),
                ),
            ),
        )

        with self.assertRaisesRegex(RuntimeError, "2 个组件失败"):
            page.close()
        self.assertEqual(["first", "second"], closed)
        owner.deleteLater()


if __name__ == "__main__":
    unittest.main()
