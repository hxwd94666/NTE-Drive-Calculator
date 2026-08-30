# 验证角色图鉴对 v30 图纸、培养、觉醒与技能关系的游戏化覆盖。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLabel,
    QPushButton,
    QTableWidget,
)

from src.features.static_catalog.domain_pages.character_page import (
    build_character_catalog_page,
)
from src.features.static_catalog.domain_pages.character_skills import SkillActionCard
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadataService,
)
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.puzzle_board import PuzzleBoardWidget


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
            game_ui_asset_root=PROJECT_ROOT / "assets" / "game_ui",
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
        self.assertEqual([], view.findChildren(QTableWidget))

        self.page.resize(760, 760)
        self.app.processEvents()
        self.assertEqual(1, view._columns)
        self.assertGreater(view._cards[0].width(), view.width() * 0.8)
        self.page.resize(1200, 760)
        self.app.processEvents()
        self.assertEqual(2, view._columns)

    def test_awakening_renders_player_facing_effects_without_raw_links(self) -> None:
        view = self.page.detail_view.awakening_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        self.app.processEvents()
        visible_text = self._visible_text(view)

        self.assertIn("技能等级加成", visible_text)
        self.assertNotIn("$.", visible_text)
        self.assertNotIn("Gameplay Effect", visible_text)
        self.assertFalse(any(
            button.text().startswith(("查看 Buff", "查看 Gameplay"))
            for button in view.findChildren(QPushButton)
        ))

    def test_skill_hints_render_directly_without_mechanics_links(self) -> None:
        skill_view = self.page.detail_view.skill_view
        self.page.detail_view.tabs.setCurrentWidget(skill_view)
        self.app.processEvents()
        card = next(
            card for card in skill_view.findChildren(SkillActionCard)
            if card.action.slot == "A"
        )
        level = card.findChild(QComboBox, "characterSkillLevel")
        toggle = card.findChild(QPushButton, "characterSkillToggle")
        self.assertIsNotNone(level)
        self.assertIsNotNone(toggle)
        assert level is not None and toggle is not None
        level.setCurrentIndex(0)
        toggle.click()
        self.app.processEvents()
        first_level = self._visible_text(card)
        level.setCurrentIndex(1)
        self.app.processEvents()
        second_level = self._visible_text(card)

        self.assertIn("当前等级倍率", first_level)
        self.assertIn("%", first_level)
        self.assertNotEqual(first_level, second_level)
        self.assertNotIn("GE_Player_Zankou_Melee1_Damage", second_level)

    def test_skills_use_official_full_width_rows_and_editable_levels(self) -> None:
        view = self.page.detail_view.skill_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        self.app.processEvents()
        rows = tuple(
            row for row in view.findChildren(SkillActionCard)
            if row.isVisibleTo(view)
        )

        self.assertEqual(["A", "E", "Q", "QTE", "G", "PASSIVE", "PASSIVE"], [
            row.action.slot for row in rows
        ])
        self.assertTrue(all(row.width() > view.width() * 0.8 for row in rows))
        self.assertTrue(all(
            row.findChild(QComboBox, "characterSkillLevel") is not None
            for row in rows if row.action.skill is not None
        ))
        self.assertFalse(any(
            button.text() == "养成"
            for row in rows for button in row.findChildren(QPushButton)
        ))
        passive_rows = tuple(row for row in rows if row.action.passive is not None)
        self.assertEqual(2, len(passive_rows))
        self.assertTrue(all(
            not row.level.isVisibleTo(row) for row in passive_rows
        ))
        passive_rows[0].set_expanded(True)
        self.app.processEvents()
        passive_text = self._visible_text(passive_rows[0].drawer)
        self.assertIn("突破 2 解锁", passive_text)
        self.assertIn("浊燃", passive_text)
        self.assertNotIn("GA_Zankou_Passive1", passive_text)

    def test_cultivation_has_only_level_and_skill_sections(self) -> None:
        detail = self.page.detail_view
        tab_labels = tuple(
            detail.tabs.tabText(index) for index in range(detail.tabs.count())
        )
        self.assertIn("养成", tab_labels)
        self.assertNotIn("等级与养成", tab_labels)
        self.assertEqual(2, detail.cultivation_tabs.count())
        self.assertEqual("等级养成", detail.cultivation_tabs.tabText(0))
        self.assertEqual("技能养成", detail.cultivation_tabs.tabText(1))
        self.assertEqual(80, detail.growth_view.end_level.count())
        self.assertEqual(80, detail.growth_view.end_level.currentData())

    def test_overview_includes_level_80_and_awakening_stage_names(self) -> None:
        detail = self.page.detail_view
        detail.tabs.setCurrentWidget(detail.overview_host)
        self.app.processEvents()
        self.assertIn("Lv.80", self._visible_text(detail.overview_host))

        detail.tabs.setCurrentWidget(detail.awakening_view)
        self.app.processEvents()
        stages = tuple(
            label.text()
            for label in detail.awakening_view.findChildren(QLabel)
            if label.objectName() == "characterAwakeningStage"
        )
        self.assertEqual(
            ("一觉", "二觉", "三觉", "四觉", "五觉", "六觉", "三觉", "六觉"),
            stages,
        )

    def test_shared_shell_owns_the_only_back_action(self) -> None:
        events: list[str | None] = []
        self.page.set_catalog_navigation_listener(
            lambda: events.append(self.page.catalog_back_label()),
        )

        self.assertEqual("角色列表", self.page.catalog_back_label())
        self.assertIsNone(self.page.findChild(QPushButton, "characterBackButton"))
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
