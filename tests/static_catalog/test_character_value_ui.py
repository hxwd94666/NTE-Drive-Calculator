# 验证角色图鉴对 v30 图纸、培养、觉醒与技能关系的游戏化覆盖。
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
    QFrame,
    QLabel,
)

from src.features.static_catalog.domain_pages.characters.page import (
    build_character_catalog_page,
)
from src.features.static_catalog.domain_pages.characters.skills import SkillActionCard
from src.services.static_catalog.characters.release_metadata import (
    CharacterReleaseMetadataService,
)
from src.services.static_catalog.characters.service import StaticCatalogCharacterService
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.catalog.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.puzzle_board import PuzzleBoardWidget


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"


class StaticCatalogCharacterValueUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.queries = StaticCatalogCharacterQueries(STATIC_DATABASE)
        self.terminology_dao = StaticGameDataDao(STATIC_DATABASE)
        self.terminology = StaticCatalogTerminologyService(self.terminology_dao)
        self.page = build_character_catalog_page(
            service=StaticCatalogCharacterService(self.queries),
            release_metadata_service=CharacterReleaseMetadataService(
                self.queries,
                self.terminology,
            ),
            game_ui_asset_root=PROJECT_ROOT / "data" / "role_catalog" / "game_ui",
            terminology_service=self.terminology,
        )
        self.page.resize(1200, 900)
        self.page.show()
        self.page.open_character(1036)
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        self.queries.close()
        self.terminology_dao.close()
        self.app.processEvents()

    def test_build_tab_covers_board_formal_guides_and_project_derivations(self) -> None:
        view = self.page.detail_view.route_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        self.app.processEvents()
        boards = view.findChildren(PuzzleBoardWidget)
        visible_text = self._visible_text(view)

        self.assertEqual(1, len(boards))
        self.assertEqual((5, 5), (
            len(boards[0].matrix), len(boards[0].matrix[0]),
        ))
        self.assertEqual(20, sum(
            value != "0" for row in boards[0].matrix for value in row
        ))
        self.assertIn("角色图纸 · 正式 5×5", visible_text)
        self.assertIn("正式培养方向", visible_text)
        self.assertIn("正式额外形状与加成", visible_text)
        self.assertIn("毕业装备模板", visible_text)
        self.assertIn("推荐权重", visible_text)
        self.assertGreaterEqual(visible_text.count("项目推荐 · 派生"), 2)

    def test_skill_hints_render_directly_without_mechanics_links(self) -> None:
        skill_view = self.page.detail_view.skill_view
        self.page.detail_view.tabs.setCurrentWidget(skill_view)
        self.app.processEvents()
        card = next(
            card for card in skill_view.findChildren(SkillActionCard)
            if card.action.slot == "A"
        )
        level = card.findChild(QComboBox, "characterSkillLevel")
        heading = card.findChild(QFrame, "characterSkillHeader")
        self.assertIsNotNone(level)
        self.assertIsNotNone(heading)
        assert level is not None and heading is not None
        level.setCurrentIndex(0)
        QTest.mouseClick(heading, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        first_level = self._visible_text(card)
        level.setCurrentIndex(1)
        self.app.processEvents()
        second_level = self._visible_text(card)
        self.assertTrue(card.drawer.isVisibleTo(card))

        self.assertIn("当前等级倍率", first_level)
        self.assertIn("%", first_level)
        self.assertNotEqual(first_level, second_level)
        self.assertNotIn("GE_Player_Zankou_Melee1_Damage", second_level)
        QTest.mouseClick(heading, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(card.drawer.isVisibleTo(card))
        heading.setFocus()
        QTest.keyClick(heading, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertTrue(card.drawer.isVisibleTo(card))
        QTest.keyClick(heading, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertFalse(card.drawer.isVisibleTo(card))

    def test_skills_use_official_order_unlocks_and_editable_levels(self) -> None:
        view = self.page.detail_view.skill_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        self.app.processEvents()
        rows = tuple(
            row for row in view.findChildren(SkillActionCard)
            if row.isVisibleTo(view)
        )

        self.assertEqual(
            [
                "A", "E", "Q", "QTE", "G",
                "PASSIVE", "PASSIVE", "PECULIARITY",
            ],
            [row.action.slot for row in rows],
        )
        self.assertTrue(all(
            row.findChild(QComboBox, "characterSkillLevel") is not None
            for row in rows if row.action.skill is not None
        ))
        passive_rows = tuple(row for row in rows if row.action.passive is not None)
        self.assertEqual(3, len(passive_rows))
        self.assertTrue(all(
            not row.level.isVisibleTo(row) for row in passive_rows
        ))
        passive_rows[0].set_expanded(True)
        self.app.processEvents()
        passive_text = self._visible_text(passive_rows[0].drawer)
        self.assertIn("突破 2 解锁", passive_text)
        self.assertIn("浊燃", passive_text)
        self.assertNotIn("GA_Zankou_Passive1", passive_text)

    def test_shared_shell_owns_the_only_back_action(self) -> None:
        events: list[str | None] = []
        self.page.set_catalog_navigation_listener(
            lambda: events.append(self.page.catalog_back_label()),
        )

        self.assertEqual("角色列表", self.page.catalog_back_label())
        self.assertTrue(self.page.catalog_go_back())
        self.assertIsNone(self.page.catalog_back_label())
        self.assertFalse(self.page.catalog_go_back())
        self.assertEqual(["角色列表", None], events)

    def test_skill_cards_never_show_missing_extra_names_or_overlap(self) -> None:
        view = self.page.detail_view.skill_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        for width in (1180, 700):
            self.page.resize(width, 820)
            self.app.processEvents()
            cards = tuple(
                card for card in view.findChildren(SkillActionCard)
                if card.isVisibleTo(view)
            )
            self.assertTrue(cards)
            self.assertNotIn("闪避反击", {card.action.slot for card in cards})
            for card in cards:
                slot = card.findChild(QLabel, "characterSkillSlot")
                title = card.findChild(QLabel, "characterSkillTitle")
                assert slot is not None and title is not None
                self.assertNotIn("名称暂未提供", title.text())
                self.assertFalse(slot.geometry().intersects(title.geometry()))

    @staticmethod
    def _visible_text(widget) -> str:
        return "\n".join(
            label.text()
            for label in widget.findChildren(QLabel)
            if label.isVisible()
        )


if __name__ == "__main__":
    unittest.main()
