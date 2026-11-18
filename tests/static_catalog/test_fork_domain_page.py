# 验证游戏化弧盘图鉴的排序、筛选、阶段和角色关系契约。
from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.app.theme import apply_app_theme
from src.domain.catalog.static_catalog import CatalogLink
from src.features.static_catalog.domain_pages.forks.page import (
    ForkOwnedResources,
    build_fork_catalog_page,
)
from src.services.static_catalog.forks.release_metadata import (
    ForkItemDisplayNameService,
    ForkProgressionState,
    build_fork_progression_request,
    fork_character_catalog_link,
    fork_mechanics_catalog_routes,
)
from src.services.static_catalog.forks.service import StaticCatalogForkService
from src.services.static_catalog.mechanics.service import StaticCatalogMechanicsService
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.progression.progression_material_card import ProgressionMaterialCard
from tests.support.qt_cleanup import dispose_windows_created_by_test as dispose_windows_created_by_test


ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / "data" / "game_static.sqlite3"
ASSETS = ROOT / "data" / "role_catalog" / "game_ui"
ROLE_DATABASE = ROOT / "data" / "role_catalog" / "game_static.sqlite3"
ROLE_ASSETS = ROOT / "data" / "role_catalog" / "game_ui"


class ForkReleaseOrderingTests(unittest.TestCase):
    def test_item_names_use_formal_projection_and_keep_unknown_explicit(self) -> None:
        dao = StaticGameDataDao(DATABASE)
        self.addCleanup(dao.close)
        service = ForkItemDisplayNameService(StaticCatalogTerminologyService(dao))
        costs = service.present_raw(
            "SkillUpMaterial_01_lv1:2,missing_material:3,gold:4,Fons:5,Gold:6",
        )

        self.assertEqual(
            ("雏鸟的希冀", "名称暂未提供", "甲硬币", "方斯", "甲硬币"),
            tuple(item.display_name for item in costs),
        )


class ForkCatalogRelationContractTests(unittest.TestCase):
    def test_character_and_structured_effect_links_are_routable(self) -> None:
        owner = fork_character_catalog_link(1036, owner=True)
        compatible = fork_character_catalog_link(1001, owner=False)
        self.assertEqual(CatalogLink("character", "1036", "owner"), owner)
        self.assertEqual(
            CatalogLink("character", "1001", "compatible"), compatible,
        )

        fork_service = StaticCatalogForkService.from_database(DATABASE)
        self.addCleanup(fork_service.close)
        detail = fork_service.get_fork("fork_GoldRecord")
        assert detail is not None
        refinement = next(row for row in detail.refinement_levels if row.level == 5)
        buffs = tuple(
            row for row in detail.buff_definitions if row.refinement_level == 5
        )
        routes = fork_mechanics_catalog_routes(refinement, buffs)

        self.assertEqual(
            {"mixing_effect", "buff"},
            {route.link.relation_kind for route in routes},
        )
        self.assertTrue(all(
            route.link.domain_key == "combat_mechanics" for route in routes
        ))
        mechanics = StaticCatalogMechanicsService(DATABASE)
        for route in routes:
            self.assertEqual(route.link.record_id, mechanics.detail(route.link.record_id).record_id)


class ForkLifecycleContractTests(unittest.TestCase):
    def test_close_all_continues_and_retries_only_failed_owner(self) -> None:
        calls: list[str] = []
        first_attempt = True

        def close_first() -> None:
            nonlocal first_attempt
            calls.append("first")
            if first_attempt:
                first_attempt = False
                raise RuntimeError("first failed")

        def close_second() -> None:
            calls.append("second")

        resources = ForkOwnedResources((close_first, close_second))
        with self.assertRaises(ExceptionGroup) as captured:
            resources.close_all()

        self.assertEqual(("first", "second"), tuple(calls))
        self.assertEqual("first failed", str(captured.exception.exceptions[0]))
        self.assertFalse(resources.closed)
        resources.close_all()
        self.assertEqual(("first", "second", "first"), tuple(calls))
        self.assertTrue(resources.closed)
        resources.close_all()
        self.assertEqual(("first", "second", "first"), tuple(calls))


class ForkCatalogPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        apply_app_theme(self.app, "black")
        self.terminology_dao = StaticGameDataDao(DATABASE)
        self.terminology = StaticCatalogTerminologyService(self.terminology_dao)
        self.addCleanup(self.terminology_dao.close)

    def test_detail_keeps_cap_states_and_shows_owner_and_compatible_roles(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        page.open_fork("fork_GoldRecord")
        self.app.processEvents()

        profile = page.profile_view
        self.assertIn("灵可", profile.owner_label.text())
        self.assertGreater(profile.compatible_character_count(), 0)
        profile.set_level(20)
        self.app.processEvents()
        self.assertEqual(2, profile.stage_button_count())
        self.assertTrue(profile.panel_values())
        profile.set_refinement(5)
        self.app.processEvents()
        self.assertIn("混频 5", profile.refinement_title.text())

    def test_reference_catalog_shows_fork_exp_and_breakthrough_image_cards(self) -> None:
        dao = StaticGameDataDao(ROLE_DATABASE)
        self.addCleanup(dao.close)
        terminology = StaticCatalogTerminologyService(dao)
        page = build_fork_catalog_page(
            database_path=ROLE_DATABASE,
            game_ui_asset_root=ROLE_ASSETS,
            terminology_service=terminology,
        )
        self.addCleanup(page.dispose)
        fork_id = page.visible_summaries()[0].fork_id
        page.open_fork(fork_id)
        page.profile_view.tabs.setCurrentIndex(1)
        page.show()
        self.app.processEvents()

        cards = page.profile_view.findChildren(ProgressionMaterialCard)
        visible_text = "\n".join(
            label.text() for label in page.profile_view.findChildren(QLabel)
            if label.isVisibleTo(page)
        )
        self.assertGreaterEqual(len(cards), 3)
        self.assertIn("500 EXP", visible_text)
        self.assertIn("2,500 EXP", visible_text)
        self.assertIn("10,000 EXP", visible_text)
        self.assertIn("使用消耗", visible_text)

        service = StaticCatalogForkService.from_database(ROLE_DATABASE)
        self.addCleanup(service.close)
        detail = service.get_fork(fork_id)
        self.assertIsNotNone(detail)
        assert detail is not None
        request = build_fork_progression_request(
            detail,
            current=ForkProgressionState(1, 0, 1),
            target=ForkProgressionState(80, 6, 1),
        )
        requested_ids = {item.item_id for item in request.requirements}
        self.assertTrue(any(
            item_id.startswith("WeaponUpMaterial_")
            for item_id in requested_ids
        ))
        self.assertNotIn(
            "level_material_relation_unavailable",
            {gap.code for gap in request.requirement_gaps},
        )

    def test_game_release_exposes_complete_fork_material_calculation(self) -> None:
        service = StaticCatalogForkService.from_database(DATABASE)
        self.addCleanup(service.close)
        detail = service.get_fork("fork_nonos")
        self.assertIsNotNone(detail)
        assert detail is not None

        request = build_fork_progression_request(
            detail,
            current=ForkProgressionState(1, 0, 1),
            target=ForkProgressionState(80, 6, 1),
        )

        requirements = {
            item.item_id: item.required_quantity
            for item in request.requirements
        }
        self.assertEqual(364, requirements["WeaponUpMaterial_lv3"])
        self.assertEqual(1, requirements["WeaponUpMaterial_lv1"])
        self.assertEqual(1_344_150, requirements["Gold"])
        self.assertNotIn(
            "level_material_relation_unavailable",
            {gap.code for gap in request.requirement_gaps},
        )

if __name__ == "__main__":
    unittest.main()
