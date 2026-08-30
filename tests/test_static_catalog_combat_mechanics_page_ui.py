# 验证战斗机制图鉴的卡片、折叠、响应式与互跳行为。
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
    QTableView,
    QTableWidget,
)

from src.app.theme import apply_app_theme, refresh_inline_theme_styles
from src.features.static_catalog.domain_pages.combat_mechanics_page import (
    build_combat_mechanics_catalog_page,
)
from src.features.static_catalog.domain_pages.mechanics_widgets import (
    CollapsiblePanel,
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

    def test_one_entry_has_six_families_cards_and_no_tables(self) -> None:
        self.assertEqual(6, len(self.page.family_buttons))
        self.assertLessEqual(
            self.page.findChild(type(self.page.gallery_page), "mechanicsControlDeck")
            .maximumHeight(),
            112,
        )
        self.assertTrue(self.page.findChildren(MechanicsGalleryCard))
        self.assertEqual([], self.page.findChildren(QTableWidget))
        self.assertEqual([], self.page.findChildren(QTableView))

    def test_narrow_page_reflows_to_one_column(self) -> None:
        self.page.resize(700, 760)
        QTest.qWait(20)
        self.app.processEvents()

        combo = self.page.findChild(QComboBox, "mechanicsFamilyCombo")
        self.assertIsNotNone(combo)
        assert combo is not None
        self.assertTrue(combo.isVisible())
        self.assertFalse(self.page.family_scroll.isVisible())
        self.assertGreater(
            self.page._card_widgets[0].width(),
            self.page.gallery_scroll.viewport().width() * 0.4,
        )

        self.page.resize(430, 760)
        self.app.processEvents()
        self.assertEqual(1, self.page._columns)
        self.assertEqual(6, combo.count())
        combo.setCurrentIndex(5)
        self.app.processEvents()
        self.assertEqual("formula", self.page._family_key)

    def test_counterfactual_evidence_chain_is_collapsed_by_default(self) -> None:
        self.page.open_record(encode_record("model", "formal_dot_classification"))
        self.app.processEvents()
        disclosure = self.page.findChild(CollapsiblePanel)

        self.assertIsNotNone(disclosure)
        assert disclosure is not None
        self.assertFalse(disclosure.toggle.isChecked())
        self.assertFalse(disclosure.body.isVisible())
        disclosure.toggle.click()
        self.app.processEvents()
        self.assertTrue(disclosure.body.isVisible())

    def test_raw_professional_identity_only_appears_in_collapsed_more_info(self) -> None:
        self.page.select_family("dot")
        self.page.search.setText("State.Damage.Dot")
        self.app.processEvents()
        effect = next(card for card in self.page._cards if card.card_kind == "effect")
        self.assertEqual("名称暂未提供", effect.title)
        self.page.open_record(effect.record_id)
        self.app.processEvents()
        disclosure = next(
            panel for panel in self.page.findChildren(CollapsiblePanel)
            if panel.property("identityDisclosure")
        )

        self.assertFalse(disclosure.toggle.isChecked())
        self.assertFalse(disclosure.body.isVisible())
        self.assertIn("更多信息", disclosure.toggle.text())

    def test_formula_expression_is_not_repeated_in_detail_hero(self) -> None:
        self.page.open_record(encode_record("formula", "dot_damage"))
        self.app.processEvents()
        expression = self.page._service.detail(
            encode_record("formula", "dot_damage")
        ).subtitle
        occurrences = sum(
            label.text() == expression
            for label in self.page.detail_host.findChildren(QLabel)
        )

        self.assertEqual(1, occurrences)

    def test_internal_and_external_links_use_typed_navigation(self) -> None:
        model_id = encode_record("model", "formal_dot_classification")
        formula_id = encode_record("formula", "dot_damage")
        self.page.open_record(model_id)
        self.page.open_link(CatalogLink("combat_mechanics", formula_id, "formula"))
        self.assertEqual(formula_id, self.page.current_record_id)

        self.page.open_link(CatalogLink("character", "1036", "owner"))
        self.assertEqual("character", self.external_links[-1].domain_key)
        self.page.go_back()
        self.assertEqual(model_id, self.page.current_record_id)

    def test_player_ui_never_renders_audit_paths_or_hashes(self) -> None:
        self.page.open_record(encode_record("formula", "dot_damage"))
        self.app.processEvents()
        visible = "\n".join(label.text() for label in self.page.findChildren(QLabel))

        self.assertNotIn("/Game/", visible)
        self.assertNotIn("src/", visible)
        self.assertNotIn("tests/", visible)
        self.assertNotIn("SHA-256", visible)
        self.assertNotIn("来源哈希", visible)

    def test_default_gallery_hides_raw_identity_and_owner_tokens(self) -> None:
        visible = "\n".join(
            label.text() for label in self.page.gallery_host.findChildren(QLabel)
        )

        for token in (
            "/Game/", "GA_", "GE_", "character_awaken", "fork_star",
            "equipment_suit", "complete", "partial", "unavailable",
            "not_applicable",
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
