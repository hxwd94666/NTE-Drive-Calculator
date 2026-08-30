# 验证角色图鉴对 v30 图纸、培养、觉醒与技能关系的游戏化覆盖。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QTableWidget

from src.features.static_catalog.domain_pages.character_page import (
    build_character_catalog_page,
)
from src.features.static_catalog.domain_pages.character_skills import SkillActionCard
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadataService,
)
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_mechanics_models import CatalogLink, decode_record
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"


class StaticCatalogCharacterValueUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.links: list[CatalogLink] = []
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
            open_catalog_link=self.links.append,
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
        cells = view.findChildren(QLabel, "characterBlueprintCell")
        visible_text = self._visible_text(view)

        self.assertEqual(25, len(cells))
        self.assertEqual(20, sum(bool(cell.property("occupied")) for cell in cells))
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

    def test_awakening_keeps_structure_collapsed_and_emits_typed_ge_link(self) -> None:
        view = self.page.detail_view.awakening_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        self.app.processEvents()
        visible_text = self._visible_text(view)

        self.assertIn("结构化正式效果", visible_text)
        self.assertIn("技能等级加成", visible_text)
        self.assertNotIn("$.", visible_text)
        link_button = next(
            button for button in view.findChildren(QPushButton)
            if button.text().startswith("查看 Buff")
        )
        link_button.click()
        self.app.processEvents()

        link = self.links[-1]
        kind, key = decode_record(link.record_id)
        self.assertEqual("combat_mechanics", link.domain_key)
        self.assertEqual("effect", kind)
        self.assertTrue(key.startswith(f"buff{chr(31)}"))

        self.page.open_character(1004)
        view = self.page.detail_view.awakening_view
        self.page.detail_view.tabs.setCurrentWidget(view)
        self.app.processEvents()
        ge_button = next(
            button for button in view.findChildren(QPushButton)
            if button.text().startswith("查看 Gameplay Effect")
        )
        ge_button.click()
        kind, key = decode_record(self.links[-1].record_id)
        self.assertEqual("effect", kind)
        self.assertTrue(key.startswith(f"gameplay_effect{chr(31)}"))

    def test_skill_hints_and_damage_items_use_typed_links_only(self) -> None:
        skill_view = self.page.detail_view.skill_view
        self.page.detail_view.tabs.setCurrentWidget(skill_view)
        self.app.processEvents()
        action = next(
            card.action for card in skill_view.findChildren(SkillActionCard)
            if card.action.slot == "A"
        )
        skill_view.drawer.show_action(action, "details")
        self.app.processEvents()
        visible_text = self._visible_text(skill_view.drawer)

        self.assertIn("正式等级提示 · 21 项", visible_text)
        self.assertIn("关联 2 个伤害项", visible_text)
        self.assertNotIn("GE_Player_Zankou_Melee1_Damage", visible_text)
        toggle = skill_view.drawer.findChild(
            QPushButton,
            "characterSkillRelationsToggle",
        )
        assert toggle is not None
        self.assertFalse(toggle.isChecked())
        toggle.click()
        self.app.processEvents()
        self.page.resize(760, 760)
        self.app.processEvents()
        relations = skill_view.drawer.findChild(
            QFrame,
            "characterSkillRelations",
        )
        assert relations is not None
        self.assertLessEqual(relations.layout().columnCount(), 2)
        damage_button = next(
            button for button in skill_view.drawer.findChildren(QPushButton)
            if button.text() == "伤害项 01"
        )
        damage_button.click()
        self.app.processEvents()

        link = self.links[-1]
        kind, key = decode_record(link.record_id)
        self.assertEqual("combat_mechanics", link.domain_key)
        self.assertEqual("effect", kind)
        self.assertTrue(key.startswith(f"skill_damage{chr(31)}"))

    @staticmethod
    def _visible_text(widget) -> str:
        return "\n".join(
            label.text()
            for label in widget.findChildren(QLabel)
            if label.isVisible()
        )


if __name__ == "__main__":
    unittest.main()
