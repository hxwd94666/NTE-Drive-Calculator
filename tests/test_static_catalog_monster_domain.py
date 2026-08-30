# 游戏资料库怪物与玩法域的公共行为测试。
from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime
from pathlib import Path

from src.services.static_catalog_monster_service import (
    CatalogFilter,
    FORMULA,
    OFFICIAL,
    UNAVAILABLE,
    StaticCatalogMonsterService,
)
from src.storage.sqlite.static_catalog_monster_queries import (
    StaticCatalogMonsterQueries,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
MAINLAND_SNAPSHOT = datetime(2026, 8, 30, 12, 0, 0)


class StaticCatalogMonsterDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StaticCatalogMonsterService.from_database(
            STATIC_DATABASE,
            mainland_now=MAINLAND_SNAPSHOT,
        )

    def tearDown(self) -> None:
        self.service.close()

    def _page(self, play_mode: str, **changes):
        filters = CatalogFilter(play_mode=play_mode, page_size=1, **changes)
        return self.service.list_entries(filters)

    def test_release_schema_coverage_is_exposed_without_mutating_static_data(self):
        self.assertEqual(35, self._page("official_illustrated").total)
        self.assertEqual(4311, self._page("template_profile").total)
        self.assertEqual(7, self._page("world_boss").total)
        self.assertEqual(32, self._page("feast").total)
        self.assertEqual(218, self._page("clone").total)
        self.assertEqual(78, self._page("high_risk").total)

        with StaticCatalogMonsterQueries(STATIC_DATABASE) as queries:
            with self.assertRaises(sqlite3.OperationalError):
                queries._connection.execute("DELETE FROM monster_catalog")

    def test_current_and_next_outer_realm_use_mainland_effective_time(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="outer_realm",
            release_scope="current_next",
            page_size=200,
        ))
        self.assertEqual(48, page.total)
        states = {entry.release_state for entry in page.items}
        config_ids = {entry.primary_id for entry in page.items}
        self.assertEqual({"current", "next"}, states)
        self.assertEqual({"Abyss_8", "Abyss_9"}, config_ids)

    def test_manual_identity_and_formula_profile_are_labeled_separately(self):
        manual = self._page("official_illustrated").items[0]
        manual_detail = self.service.get_detail(manual.key)
        self.assertIsNotNone(manual_detail)
        self.assertEqual(
            OFFICIAL,
            manual_detail.sections[0].values[0].provenance,
        )

        profile = self.service.list_entries(CatalogFilter(
            play_mode="template_profile",
            search="boss_04_BP_DiyBoss",
            page_size=10,
        )).items[0]
        profile_detail = self.service.get_detail(profile.key)
        self.assertIsNotNone(profile_detail)
        formula_values = [
            value
            for section in profile_detail.sections
            for value in section.values
            if value.provenance == FORMULA
        ]
        self.assertTrue(formula_values)
        self.assertIn("共用数值画像", profile_detail.notices[0])

    def test_formula_profile_reports_attack_tier_as_unavailable(self):
        feast = self._page("feast").items[0]
        detail = self.service.get_detail(feast.key)
        attack_tier = next(
            value
            for section in detail.sections
            for value in section.values
            if value.label == "攻击档"
        )
        self.assertEqual(UNAVAILABLE, attack_tier.provenance)
        self.assertIn("schema v29", attack_tier.value)

    def test_profile_to_gameplay_jump_uses_exact_official_reference(self):
        profile = self.service.list_entries(CatalogFilter(
            play_mode="template_profile",
            search="boss_04_BP_DiyBoss",
            page_size=10,
        )).items[0]
        detail = self.service.get_detail(profile.key)
        feast_links = [
            relation
            for relation in detail.relations
            if relation.target_key.startswith("feast|")
        ]
        self.assertEqual(4, len(feast_links))
        self.assertTrue(all(
            relation.relation_kind == "exact_official_template_id"
            for relation in feast_links
        ))

    def test_high_risk_without_difficulty_pool_is_explicitly_unavailable(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="high_risk",
            search="AdvVision_HeheBear",
            page_size=10,
        ))
        self.assertEqual(6, page.total)
        detail = self.service.get_detail(page.items[0].key)
        unavailable = [
            value
            for section in detail.sections
            for value in section.values
            if value.provenance == UNAVAILABLE
        ]
        self.assertTrue(unavailable)
        self.assertTrue(any("通用回退池" in value.value for value in unavailable))

    def test_exact_ids_and_paths_are_searchable_and_copyable(self):
        feast = self.service.list_entries(CatalogFilter(
            search="DiyBossStage8",
            play_mode="feast",
            page_size=10,
        ))
        self.assertEqual(4, feast.total)
        detail = self.service.get_detail(feast.items[0].key)
        official_ids = [
            value
            for section in detail.sections
            for value in section.values
            if value.copyable and value.provenance == OFFICIAL
        ]
        self.assertTrue(any(value.value == "DiyBossStage8" for value in official_ids))

    def test_clone_difficulty_without_category_remains_visible(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="clone",
            search="BidKing1",
            page_size=10,
        ))
        self.assertEqual(1, page.total)
        detail = self.service.get_detail(page.items[0].key)
        category = next(
            value
            for section in detail.sections
            for value in section.values
            if value.label == "类目"
        )
        self.assertEqual(UNAVAILABLE, category.provenance)


if __name__ == "__main__":
    unittest.main()
