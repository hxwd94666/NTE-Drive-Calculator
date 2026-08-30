# 验证游戏化弧盘图鉴的排序、筛选、阶段和角色关系契约。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from src.app.theme import apply_app_theme
from src.domain.progression_stamina import (
    IdentificationLevelProjection,
    MaterialDeficit,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)
from src.domain.static_catalog import CatalogLink
from src.domain.static_catalog_terminology import LocalizedForkCampaign, LocalizedTerm
from src.features.static_catalog.domain_pages.fork_page import (
    ForkCatalogPage,
    ForkOwnedResources,
    build_fork_catalog_page,
)
from src.services.static_catalog_fork_release_metadata import (
    ForkItemDisplayNameService,
    ForkProgressionRequest,
    ForkProgressionState,
    build_fork_progression_request,
    fork_character_catalog_link,
    fork_mechanics_catalog_routes,
    sort_fork_catalog,
)
from src.services.static_catalog_fork_service import ForkCost, StaticCatalogForkService
from src.services.static_catalog_mechanics_service import StaticCatalogMechanicsService
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "game_static.sqlite3"
ASSETS = ROOT / "assets" / "game_ui"


class ForkReleaseOrderingTests(unittest.TestCase):
    def test_limited_forks_are_newest_first_before_stable_launch_batch(self) -> None:
        service = StaticCatalogForkService.from_database(DATABASE)
        self.addCleanup(service.close)
        terminology_dao = StaticGameDataDao(DATABASE)
        self.addCleanup(terminology_dao.close)
        campaigns = StaticCatalogTerminologyService(
            terminology_dao,
        ).list_fork_campaigns()
        ordered = sort_fork_catalog(
            service.list_forks(page_size=200).items,
            campaigns,
        )

        limited_ids = tuple(item.featured_fork_id for item in campaigns)
        self.assertEqual(8, len(limited_ids))
        self.assertEqual(limited_ids, tuple(item.fork_id for item in ordered[:8]))
        regular = ordered[8:]
        expected = sorted(
            regular,
            key=lambda item: (
                {"ORANGE": 0, "PURPLE": 1, "BLUE": 2}.get(item.quality, 99),
                item.fork_type_id if item.fork_type_id is not None else 999,
                item.name_zh,
                item.fork_id,
            ),
        )
        self.assertEqual(expected, list(regular))

    def test_item_names_use_formal_projection_and_keep_unknown_explicit(self) -> None:
        dao = StaticGameDataDao(DATABASE)
        self.addCleanup(dao.close)
        service = ForkItemDisplayNameService(StaticCatalogTerminologyService(dao))
        costs = service.present_raw(
            "SkillUpMaterial_01_lv1:2,missing_material:3,gold:4,Fons:5,Gold:6",
        )

        self.assertEqual(
            ("雏鸟的希冀", "名称暂未提供", "方斯", "方斯", "甲硬币"),
            tuple(item.display_name for item in costs),
        )


class ForkProgressionContractTests(unittest.TestCase):
    def test_repeat_campaign_uses_pool_identity_and_newest_fork_order(self) -> None:
        def campaign(pool_id: str, fork_id: str, ordinal: int):
            return LocalizedForkCampaign(
                pool_id=pool_id,
                featured_fork_id=fork_id,
                release_ordinal=ordinal,
                title=LocalizedTerm(
                    entity_kind="fork_campaign",
                    requested_id=pool_id,
                    canonical_id=pool_id,
                    requested_locale="zh-CN",
                    resolved_locale="zh-CN",
                    display_name=pool_id,
                    status="complete",
                ),
            )

        campaigns = (
            campaign("pool_repeat", "fork_a", 3),
            campaign("pool_b", "fork_b", 2),
            campaign("pool_original", "fork_a", 1),
        )
        summaries = (
            SimpleNamespace(
                fork_id="fork_b", quality="BLUE", fork_type_id=1, name_zh="B",
            ),
            SimpleNamespace(
                fork_id="fork_a", quality="BLUE", fork_type_id=1, name_zh="A",
            ),
        )

        self.assertEqual(
            ("fork_a", "fork_b"),
            tuple(item.fork_id for item in sort_fork_catalog(summaries, campaigns)),
        )
        self.assertEqual(
            ("pool_repeat", "pool_original"),
            tuple(item.pool_id for item in campaigns if item.featured_fork_id == "fork_a"),
        )

    def test_request_aggregates_known_costs_and_preserves_unknowns(self) -> None:
        detail = SimpleNamespace(
            summary=SimpleNamespace(fork_id="fork_contract"),
            breakthroughs=(
                SimpleNamespace(
                    stage=1,
                    item_costs=(ForkCost("material_a", 2, "2"),),
                    gold_costs=(ForkCost("gold", 100, "100"),),
                ),
                SimpleNamespace(
                    stage=2,
                    item_costs=(ForkCost("material_a", 3, "3"),),
                    gold_costs=(),
                ),
            ),
            refinement_levels=(
                SimpleNamespace(level=2, need_gold_raw="gold:10"),
                SimpleNamespace(level=3, need_gold_raw="material_unknown:?"),
            ),
            growth_levels=tuple(
                SimpleNamespace(level=level, need_exp=5)
                for level in range(21, 31)
            ),
        )
        request = build_fork_progression_request(
            detail,
            current=ForkProgressionState(20, 0, 1),
            target=ForkProgressionState(30, 2, 3),
        )

        self.assertIsInstance(request, ForkProgressionRequest)
        self.assertEqual(50, request.required_upgrade_exp)
        requirements = {item.item_id: item for item in request.requirements}
        self.assertEqual(5, requirements["material_a"].required_quantity)
        self.assertEqual(110, requirements["gold"].required_quantity)
        self.assertIsNone(requirements["material_unknown"].required_quantity)
        self.assertEqual(0, requirements["material_unknown"].known_quantity)
        self.assertEqual(
            {"official_quantity_unavailable", "level_material_relation_unavailable"},
            {gap.code for gap in request.requirement_gaps},
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

    def test_factory_builds_all_cards_and_type_filter(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        self.app.processEvents()

        self.assertIsInstance(page, ForkCatalogPage)
        self.assertEqual(49, len(page.visible_summaries()))
        fork_type = page.fork_types()[0]
        page.set_type_filter(fork_type.fork_type_id)
        self.app.processEvents()
        self.assertTrue(page.visible_summaries())
        self.assertTrue(all(
            item.fork_type_id == fork_type.fork_type_id
            for item in page.visible_summaries()
        ))

    def test_search_quality_filter_and_responsive_columns(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        page.resize(1180, 820)
        page.show()
        self.app.processEvents()
        wide_columns = page.gallery_column_count()

        self.assertFalse(page.filter_panel.isVisible())
        page.filter_toggle.click()
        self.app.processEvents()
        self.assertTrue(page.filter_panel.isVisible())
        page.set_search_query("GoldRecord")
        self.assertEqual(
            ("fork_GoldRecord",),
            tuple(item.fork_id for item in page.visible_summaries()),
        )
        page.set_search_query("")
        page.set_quality_filter("ORANGE")
        self.assertTrue(page.visible_summaries())
        self.assertTrue(all(
            item.quality == "ORANGE" for item in page.visible_summaries()
        ))

        page.resize(620, 820)
        self.app.processEvents()
        self.assertLess(page.gallery_column_count(), wide_columns)
        self.assertGreaterEqual(page.gallery_column_count(), 2)

    def test_all_fork_cards_resolve_official_images(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)

        self.assertEqual((49, 49), page.image_coverage())

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

    def test_character_and_effect_relationships_render_inline_without_jumps(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        page.open_fork("fork_GoldRecord")
        page.profile_view.set_refinement(5)
        self.app.processEvents()

        visible_text = "\n".join(
            label.text() for label in page.profile_view.findChildren(QLabel)
            if label.isVisibleTo(page)
        )
        self.assertIn("灵可", visible_text)
        self.assertIn("效果内容", visible_text)
        self.assertFalse(any(
            button.text() in {"查看角色", "查看战斗机制", "更多信息"}
            for button in page.profile_view.findChildren(QPushButton)
        ))

    def test_detail_hides_developer_fields_and_resolves_skill_values(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        page.open_fork("fork_GoldRecord")
        page.resize(1280, 860)
        page.show()
        self.app.processEvents()

        visible_text = "\n".join(
            label.text() for label in page.profile_view.findChildren(QLabel)
            if label.isVisibleTo(page)
        )
        for forbidden in (
            "schema", "importer", "/Game/", "Calculation",
            "EGameplay", "requirement", "BUFF_EVENT_",
        ):
            self.assertNotIn(forbidden, visible_text)
        self.assertNotIn("{0}", visible_text)
        self.assertNotIn("fork_GoldRecord", visible_text)
        self.assertIn("生效时机", visible_text)
        self.assertIn("效果内容", visible_text)

        self.assertFalse(any(
            button.text() == "更多信息"
            for button in page.profile_view.findChildren(QPushButton)
        ))

    def test_narrow_cultivation_has_no_horizontal_overflow(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        page.resize(700, 920)
        page.open_fork("fork_GoldRecord")
        page.profile_view.tabs.setCurrentIndex(1)
        page.show()
        self.app.processEvents()

        cultivation = page.profile_view.tabs.widget(1)
        self.assertEqual(0, cultivation.horizontalScrollBar().maximum())
        self.assertTrue(page.profile_view.hero.isVisible())
        self.assertGreater(page.profile_view.hero.width(), 600)
        self.assertFalse(page.profile_view.art.pixmap().isNull())
        self.assertEqual("弧盘列表", page.catalog_back_label())
        self.assertIsNone(page.profile_view.findChild(QPushButton, "forkBackButton"))
        visible_text = "\n".join(
            label.text() for label in page.profile_view.findChildren(QLabel)
            if label.isVisibleTo(page)
        )
        self.assertIn("方斯", visible_text)
        self.assertIn("罐装液态梦", visible_text)
        self.assertIn("悖谬絮语", visible_text)
        self.assertNotIn("名称暂未提供", visible_text)
        for raw_value in (
            "WeaponBreakMaterial", "OrdinaryMonMaterial", "gold", "金币",
            "fork_GoldRecord",
        ):
            self.assertNotIn(raw_value, visible_text)

        self.assertEqual(
            [], page.profile_view.findChildren(QLabel, "forkRawIdentity")
        )

    def test_progression_request_and_result_use_symmetric_public_contract(self) -> None:
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        payloads: list[object] = []
        page.progression_requested.connect(payloads.append)
        page.open_fork("fork_GoldRecord")
        controls = page.profile_view.progression_controls
        controls.current_level.setValue(20)
        controls.target_level.setValue(30)
        controls.target_stage.setCurrentIndex(controls.target_stage.findData(2))
        controls.target_mixing.setValue(3)
        controls.request.click()
        self.app.processEvents()

        self.assertEqual(1, len(payloads))
        request = payloads[0]
        self.assertIsInstance(request, ForkProgressionRequest)
        self.assertEqual(ForkProgressionState(20, 0, 1), request.current)
        self.assertEqual(ForkProgressionState(30, 2, 3), request.target)
        self.assertTrue(request.requirements)

        result = ProgressionStaminaResult(
            status=StaminaPlanStatus.PARTIAL,
            identification=IdentificationLevelProjection(60, 7, 7, False),
            deficits=(MaterialDeficit("gold", 100, 20, 80),),
            runs=(),
            known_stamina=40,
            total_stamina=None,
            unresolved_item_ids=("missing_material",),
            gaps=("material_yield_unavailable",),
        )
        self.assertFalse(page.set_progression_result(
            fork_id="fork_other",
            result=result,
        ))
        self.assertTrue(page.set_progression_result(
            fork_id="fork_GoldRecord",
            result=result,
        ))
        self.assertIn("部分可用", controls.result.text())
        self.assertIn("已知活力：40", controls.result.text())
        self.assertNotIn("gold", controls.result.text())
        self.assertNotIn("missing_material", controls.result.text())
        self.assertNotIn("material_yield_unavailable", controls.result.text())

        self.assertTrue(page.catalog_go_back())
        self.assertIsNone(page.catalog_back_label())
        self.assertFalse(page.set_progression_result(
            fork_id="fork_GoldRecord",
            result=result,
        ))
        page.open_fork("fork_Time")
        self.assertFalse(page.set_progression_result(
            fork_id="fork_GoldRecord",
            result=result,
        ))
        self.assertTrue(page.set_progression_result(
            fork_id="fork_Time",
            result=result,
        ))

    def test_light_profile_hero_uses_light_mapped_background(self) -> None:
        apply_app_theme(self.app, "light")
        page = build_fork_catalog_page(
            database_path=DATABASE,
            game_ui_asset_root=ASSETS,
            terminology_service=self.terminology,
        )
        self.addCleanup(page.dispose)
        page.open_fork("fork_GoldRecord")
        page.resize(1280, 860)
        page.show()
        self.app.processEvents()

        style = page.profile_view.hero.styleSheet().lower()
        self.assertIn("#ddf4ff", style)
        self.assertNotIn("#241642", style)


if __name__ == "__main__":
    unittest.main()
