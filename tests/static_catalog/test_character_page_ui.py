# 验证角色技能的未知消耗、等级刷新、键盘操作与窄窗可用性。
from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.features.static_catalog.domain_pages.characters.page import (
    build_character_catalog_page,
)
from src.features.static_catalog.domain_pages.characters.skills import SkillActionCard
from src.services.progression.character_requirements import (
    project_skill_level_requirements,
)
from src.services.static_catalog.characters.models import CostItem
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


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"


class CharacterSkillRequirementProjectionTests(unittest.TestCase):
    def test_skill_requirements_keep_interval_totals_and_hidden_quantity_gap(self) -> None:
        queries = StaticCatalogCharacterQueries(STATIC_DATABASE)
        self.addCleanup(queries.close)
        detail = StaticCatalogCharacterService(queries).get_character_detail(1036)
        self.assertIsNotNone(detail)
        assert detail is not None
        skill = next(
            skill for skill in detail.skills if skill.skill_id == "GA_Zankou_Melee"
        )
        # 直接保护跨等级累计，旧 UI 的分隔符和中文文案不再承担计算验收。
        full = project_skill_level_requirements(
            skill, from_level=1, to_level=10, terminology=None,
        )
        self.assertEqual("complete", full.status.value)
        self.assertEqual({
            "gold": 437_000, "SkillUpMaterial_03_lv1": 10,
            "SkillUpMaterial_03_lv2": 10, "SkillUpMaterial_03_lv3": 16,
            "OrdinaryMonMaterial_03_lv1": 10, "OrdinaryMonMaterial_03_lv2": 10,
            "OrdinaryMonMaterial_03_lv3": 16, "weeklycloneboss_a03_01": 8,
        }, {row.item_id: row.required_quantity for row in full.requirements})
        level_one = next(level for level in skill.levels if level.level == 1)
        # 正式行标记数量隐藏时，零只是占位；材料汇总必须保留缺口而非当作免费。
        hidden_level = replace(
            level_one,
            costs=(CostItem("HiddenFormalItem", 0, hidden_amount=True),),
        )
        projection = project_skill_level_requirements(
            replace(skill, levels=(hidden_level,)),
            from_level=1,
            to_level=2,
            terminology=None,
        )

        self.assertEqual("unavailable", projection.status.value)
        self.assertEqual((), projection.requirements)
        self.assertEqual(
            (("skill_cost_quantity_hidden", 2, "HiddenFormalItem"),),
            tuple((gap.reason_code, gap.level, gap.item_id) for gap in projection.gaps),
        )


class StaticCatalogCharacterPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.queries = StaticCatalogCharacterQueries(STATIC_DATABASE)
        self.terminology_dao = StaticGameDataDao(STATIC_DATABASE)
        terminology = StaticCatalogTerminologyService(self.terminology_dao)
        self.page = build_character_catalog_page(
            service=StaticCatalogCharacterService(self.queries),
            release_metadata_service=CharacterReleaseMetadataService(
                self.queries,
                terminology,
            ),
            game_ui_asset_root=PROJECT_ROOT / "data" / "role_catalog" / "game_ui",
            terminology_service=terminology,
        )
        self.page.resize(1200, 900)
        self.page.show()
        self.page.open_character(1036)
        self.page.detail_view.tabs.setCurrentWidget(self.page.detail_view.skill_view)
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        self.queries.close()
        self.terminology_dao.close()
        self.app.processEvents()

    def test_skill_levels_refresh_and_details_remain_keyboard_accessible(self) -> None:
        view = self.page.detail_view.skill_view
        card = next(
            card for card in view.findChildren(SkillActionCard)
            if card.action.slot == "A"
        )
        card.level.setCurrentIndex(0)
        QTest.mouseClick(card.heading, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(card.drawer.isVisibleTo(card))
        first_rates = tuple(
            label.text() for label in card.drawer.findChildren(QLabel)
            if label.isVisibleTo(card) and "%" in label.text()
        )

        card.level.setCurrentIndex(1)
        self.app.processEvents()
        second_rates = tuple(
            label.text() for label in card.drawer.findChildren(QLabel)
            if label.isVisibleTo(card) and "%" in label.text()
        )
        # 比较倍率内容而不是等级标题，防止仅刷新标题却沿用旧等级数值。
        self.assertTrue(first_rates)
        self.assertNotEqual(first_rates, second_rates)

        QTest.mouseClick(card.heading, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(card.drawer.isVisibleTo(card))
        card.heading.setFocus()
        QTest.keyClick(card.heading, Qt.Key.Key_Return)
        self.app.processEvents()
        self.assertTrue(card.drawer.isVisibleTo(card))
        QTest.keyClick(card.heading, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertFalse(card.drawer.isVisibleTo(card))

    def test_skill_controls_and_expanded_details_fit_common_widths(self) -> None:
        view = self.page.detail_view.skill_view
        for width in (1180, 700):
            self.page.resize(width, 820)
            self.app.processEvents()
            cards = tuple(
                card for card in view.findChildren(SkillActionCard)
                if card.isVisibleTo(view)
            )
            self.assertTrue(cards)
            for card in cards:
                controls = tuple(
                    widget for widget in card.heading.findChildren(
                        QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly,
                    )
                    if widget.isVisibleTo(card)
                )
                # 只保护文字和等级控件未被裁掉或互相遮挡，不固定行宽、顺序或布局字段。
                for index, control in enumerate(controls):
                    self.assertTrue(card.heading.rect().contains(control.geometry()))
                    self.assertTrue(all(
                        not control.geometry().intersects(other.geometry())
                        for other in controls[index + 1:]
                    ))
            card = next(card for card in cards if card.action.slot == "A")
            card.set_expanded(True)
            self.app.processEvents()
            self.assertTrue(card.drawer.isVisibleTo(view))
            self.assertTrue(card.rect().contains(card.drawer.geometry()))


if __name__ == "__main__":
    unittest.main()
