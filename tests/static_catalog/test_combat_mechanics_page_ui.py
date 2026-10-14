# 验证机制公式展示、不可用记录、窄窗键盘操作与导航回退。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
)

from src.app.theme import apply_app_theme
from src.features.static_catalog.domain_pages.mechanics.page import (
    build_combat_mechanics_catalog_page,
)
from src.services.static_catalog.mechanics.service import encode_record


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class StaticCatalogCombatMechanicsPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        apply_app_theme(self.app, "dark")
        self.page = build_combat_mechanics_catalog_page(
            database_path=PROJECT_ROOT / "data" / "game_static.sqlite3",
            game_ui_asset_root=PROJECT_ROOT / "data" / "role_catalog" / "game_ui",
            open_catalog_link=lambda _link: None,
        )
        self.page.resize(1280, 850)
        self.page.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.dispose()
        self.page.close()
        self.page.deleteLater()


    def test_narrow_family_picker_remains_keyboard_accessible(self) -> None:
        self.page.resize(430, 760)
        QTest.qWait(20)
        self.app.processEvents()
        combo = self.page.findChild(QComboBox, "mechanicsFamilyCombo")
        assert combo is not None
        self.assertTrue(combo.isVisible() and combo.isEnabled())
        self.assertTrue(combo.parentWidget().contentsRect().contains(combo.geometry()))
        combo.setFocus()
        QTest.keyClick(combo, Qt.Key_End)
        self.app.processEvents()
        self.assertEqual(combo.count() - 1, combo.currentIndex())

    def test_formula_detail_never_renders_internal_expression_or_raw_buttons(self) -> None:
        self.assertTrue(self.page.open_record(
            encode_record("formula", "skill_multiplier")
        ))
        self.app.processEvents()
        visible = "\n".join(
            label.text() for label in self.page.detail_host.findChildren(QLabel)
        )
        self.assertTrue(visible.strip())
        for token in (
            "CoefModify", "SourceTierCoef", "正式静态", "更多信息", "反事实",
            "/Game/", "GA_", "GE_",
        ):
            self.assertNotIn(token, visible)


    def test_missing_buff_link_is_ignored_without_changing_page(self) -> None:
        self.assertIs(self.page.stack.currentWidget(), self.page.gallery_page)
        record_id = encode_record("effect", f"buff{chr(31)}missing_buff")
        self.assertFalse(self.page.open_record(record_id))
        self.assertIs(self.page.stack.currentWidget(), self.page.gallery_page)
        self.assertIsNone(self.page.current_record_id)


    def test_counterfactual_record_is_not_openable(self) -> None:
        self.assertFalse(self.page.open_record(
            encode_record("model", "formal_dot_classification")
        ))
        self.assertIsNone(self.page.current_record_id)


    def test_single_shell_navigation_contract_returns_to_mechanism_list(self) -> None:
        updates: list[str] = []
        self.page.set_catalog_navigation_listener(lambda: updates.append("updated"))
        self.assertIsNone(self.page.catalog_back_label())
        self.assertTrue(self.page.open_record(
            encode_record("formula", "direct_damage")
        ))
        self.assertTrue(self.page.open_record(encode_record("formula", "critical")))
        self.assertTrue(self.page.catalog_back_label())
        # 详情历史回退返回上一公式；外层返回契约则直接退出到机制列表。
        self.page.go_back()
        self.assertEqual(encode_record("formula", "direct_damage"), self.page.current_record_id)
        self.assertTrue(self.page.catalog_go_back())
        self.assertIsNone(self.page.catalog_back_label())
        self.assertFalse(self.page.catalog_go_back())
        self.assertGreaterEqual(len(updates), 2)


if __name__ == "__main__":
    unittest.main()
