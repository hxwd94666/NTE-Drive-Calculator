# 验证战斗机制图鉴的高密度中文公式、失效关系保护与响应式布局。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QTableView,
    QTableWidget,
)

from src.app.theme import apply_app_theme, refresh_inline_theme_styles
from src.features.static_catalog.domain_pages.combat_mechanics_page import (
    build_combat_mechanics_catalog_page,
)
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    CollapsiblePanel,
    LinkButton,
    MechanicsGalleryCard,
)
from src.services.static_catalog_mechanics_service import CatalogLink, encode_record


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StaticCatalogCombatMechanicsPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        apply_app_theme(self.app, "dark")
        self.external_links: list[CatalogLink] = []
        self.page = build_combat_mechanics_catalog_page(
            database_path=PROJECT_ROOT / "data" / "game_static.sqlite3",
            game_ui_asset_root=PROJECT_ROOT / "assets" / "game_ui",
            open_catalog_link=self.external_links.append,
        )
        self.page.resize(1280, 850)
        self.page.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.dispose()
        self.page.close()
        self.page.deleteLater()

    def test_one_entry_has_four_dense_formula_families_and_no_tables(self) -> None:
        self.assertEqual(4, len(self.page.family_buttons))
        self.assertLessEqual(
            self.page.findChild(type(self.page.gallery_page), "mechanicsControlDeck")
            .maximumHeight(),
            94,
        )
        cards = self.page.findChildren(MechanicsGalleryCard)
        self.assertTrue(cards)
        self.assertTrue(all(card.minimumHeight() <= 112 for card in cards))
        self.assertEqual([], self.page.findChildren(QTableWidget))
        self.assertEqual([], self.page.findChildren(QTableView))

    def test_narrow_page_keeps_all_families_and_reflows_without_overflow(self) -> None:
        self.page.resize(700, 760)
        QTest.qWait(20)
        self.app.processEvents()
        combo = self.page.findChild(QComboBox, "mechanicsFamilyCombo")
        assert combo is not None
        self.assertTrue(combo.isVisible())
        self.assertFalse(self.page.family_scroll.isVisible())
        self.assertEqual(4, combo.count())
        self.assertGreaterEqual(self.page._columns, 2)

        self.page.resize(430, 760)
        QTest.qWait(20)
        self.app.processEvents()
        self.assertEqual(1, self.page._columns)
        combo.setCurrentIndex(3)
        self.app.processEvents()
        self.assertEqual("settlement", self.page._family_key)

    def test_formula_detail_never_renders_internal_expression_or_raw_buttons(self) -> None:
        self.assertTrue(self.page.open_record(
            encode_record("formula", "skill_multiplier")
        ))
        self.app.processEvents()
        visible = "\n".join(
            label.text() for label in self.page.detail_host.findChildren(QLabel)
        )
        self.assertIn("技能倍率 = 等级倍率", visible)
        for token in (
            "CoefModify", "SourceTierCoef", "正式静态", "更多信息", "反事实",
            "/Game/", "GA_", "GE_",
        ):
            self.assertNotIn(token, visible)
        self.assertEqual([], self.page.detail_host.findChildren(CollapsiblePanel))
        self.assertEqual([], self.page.detail_host.findChildren(LinkButton))

    def test_missing_buff_link_is_ignored_without_changing_page(self) -> None:
        self.assertIs(self.page.stack.currentWidget(), self.page.gallery_page)
        record_id = encode_record("effect", f"buff{chr(31)}missing_buff")
        self.assertFalse(self.page.open_record(record_id))
        self.assertIs(self.page.stack.currentWidget(), self.page.gallery_page)
        self.assertIsNone(self.page.current_record_id)

    def test_owned_effect_is_rendered_inline_without_navigation_button(self) -> None:
        record_id = encode_record(
            "effect", f"combat_effect{chr(31)}character_awaken:1036:Effect1"
        )
        self.assertTrue(self.page.open_record(record_id))
        self.app.processEvents()
        visible = "\n".join(
            label.text() for label in self.page.detail_host.findChildren(QLabel)
        )
        self.assertIn("所属对象", visible)
        self.assertEqual([], self.page.detail_host.findChildren(LinkButton))
        self.assertFalse(any(
            "前往" in button.text()
            for button in self.page.detail_host.findChildren(QPushButton)
        ))
        self.assertEqual([], self.external_links)

    def test_counterfactual_record_is_not_openable(self) -> None:
        self.assertFalse(self.page.open_record(
            encode_record("model", "formal_dot_classification")
        ))
        self.assertIsNone(self.page.current_record_id)

    def test_history_back_returns_to_previous_formula_then_gallery(self) -> None:
        direct = encode_record("formula", "direct_damage")
        critical = encode_record("formula", "critical")
        self.assertTrue(self.page.open_record(direct))
        self.assertTrue(self.page.open_record(critical))
        self.page.go_back()
        self.assertEqual(direct, self.page.current_record_id)
        self.page.go_back()
        self.assertIs(self.page.stack.currentWidget(), self.page.gallery_page)

    def test_single_shell_navigation_contract_returns_to_mechanism_list(self) -> None:
        updates: list[str] = []
        self.page.set_catalog_navigation_listener(lambda: updates.append("updated"))
        self.assertIsNone(self.page.catalog_back_label())
        self.assertTrue(self.page.open_record(
            encode_record("formula", "direct_damage")
        ))
        self.assertEqual("机制列表", self.page.catalog_back_label())
        self.assertTrue(self.page.catalog_go_back())
        self.assertIsNone(self.page.catalog_back_label())
        self.assertFalse(self.page.catalog_go_back())
        self.assertGreaterEqual(len(updates), 2)

    def test_external_navigation_contract_remains_explicit_only(self) -> None:
        link = CatalogLink("character", "1036", "owner")
        self.page.open_link(link)
        self.assertEqual([link], self.external_links)

    def test_default_gallery_has_no_skill_effect_or_audit_cards(self) -> None:
        visible = "\n".join(
            label.text() for label in self.page.gallery_host.findChildren(QLabel)
        )
        for token in (
            "GAMEPLAY", "BUFF", "GA_", "GE_", "反事实", "正式静态",
            "项目公式", "complete", "partial", "unavailable",
        ):
            self.assertNotIn(token, visible)

    def test_inline_styles_refresh_for_dark_black_and_white(self) -> None:
        for theme in ("dark", "black", "light"):
            apply_app_theme(self.app, theme)
            refresh_inline_theme_styles(self.page, self.app)
            self.app.processEvents()
            self.assertTrue(self.page.styleSheet())
            self.assertTrue(self.page.findChildren(MechanicsGalleryCard))


if __name__ == "__main__":
    unittest.main()
