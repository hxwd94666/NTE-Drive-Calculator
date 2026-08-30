# 验证游戏资料库角色域的只读查询、DTO 投影与分页契约。
from __future__ import annotations

import unittest
from pathlib import Path

from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.storage.sqlite.static_catalog_character_queries import (
    StaticCatalogCharacterQueries,
)


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"


class StaticCatalogCharacterDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queries = StaticCatalogCharacterQueries(STATIC_DATABASE)
        self.service = StaticCatalogCharacterService(self.queries)

    def tearDown(self) -> None:
        self.queries.close()

    def test_character_search_follows_formal_ga_ge_buff_and_resource_links(self) -> None:
        by_ga = self.service.list_characters(query="GA_Zankou_Skill", limit=5)
        by_ge = self.service.list_characters(
            query="GE_Player_Zankou_DotDamage",
            limit=5,
        )
        by_buff = self.service.list_characters(
            query="Buff_Zankou_MagicEffect",
            limit=5,
        )
        by_path = self.service.list_characters(
            query="Ability_036_Zankou",
            limit=5,
        )

        for page in (by_ga, by_ge, by_buff, by_path):
            self.assertIn(1036, {item.character_id for item in page.items})
            self.assertTrue(page.dataset.dataset_id)
            self.assertEqual(29, page.dataset.schema_version)

    def test_character_search_treats_sql_wildcards_as_literal_text(self) -> None:
        page = self.service.list_characters(query="%_", limit=200)

        self.assertEqual(0, page.total)
        self.assertEqual((), page.items)

    def test_detail_projects_growth_breakthrough_and_known_data_gaps(self) -> None:
        detail = self.service.get_character_detail(1036)

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual("残虹", detail.character.name_zh)
        self.assertEqual("咒", detail.character.element_label)
        self.assertEqual(86, detail.growth_count)
        self.assertEqual((20, 30, 40, 50, 60, 70), tuple(
            stage.level for stage in detail.breakthroughs
        ))
        self.assertTrue(all(
            stage.before.state == "breakthrough_before"
            and stage.after.state == "breakthrough_after"
            for stage in detail.breakthroughs
        ))
        gaps = {gap.field_key: gap for gap in detail.gaps}
        self.assertEqual("unavailable", gaps["character_level_costs"].status)
        self.assertEqual("unavailable", gaps["character_breakthrough_costs"].status)
        self.assertEqual("partial", gaps["material_item_names"].status)

    def test_detail_keeps_structured_awakening_and_skill_cost_ids(self) -> None:
        detail = self.service.get_character_detail(1036)

        assert detail is not None
        ordinary = tuple(
            effect for effect in detail.awakenings
            if effect.awaken_type == "Awaken_Effect"
        )
        resonance = tuple(
            effect for effect in detail.awakenings
            if effect.awaken_type == "Awaken_Resonance"
        )
        self.assertEqual(6, len(ordinary))
        self.assertEqual(2, len(resonance))
        self.assertGreater(len(ordinary[0].structured_effects), 0)
        self.assertTrue(any(
            field.path.endswith(".Buff.AssetPathName")
            and "Buff_Zankou_Level1" in field.value_json
            for field in ordinary[0].structured_effects
        ))
        melee = next(skill for skill in detail.skills if skill.skill_id == "GA_Zankou_Melee")
        self.assertEqual(9, len(melee.levels))
        first_costs = {item.item_id: item.quantity for item in melee.levels[0].costs}
        self.assertEqual(2000.0, first_costs["gold"])
        self.assertEqual(2.0, first_costs["SkillUpMaterial_03_lv1"])

    def test_graduation_and_cultivation_keep_formal_fork_association(self) -> None:
        detail = self.service.get_character_detail(1036)

        assert detail is not None
        self.assertIsNotNone(detail.cultivation)
        self.assertIsNotNone(detail.graduation)
        assert detail.graduation is not None
        self.assertEqual("fork_DemonBlade", detail.graduation.fork_id)
        self.assertEqual("噬心诡刃", detail.graduation.fork_name_zh)
        self.assertTrue(any("fork_DemonBlade" in path for path in detail.graduation.fork_paths))

    def test_growth_and_combat_relationships_are_independently_paginated(self) -> None:
        first_growth = self.service.list_growth(1036, limit=10, offset=0)
        second_growth = self.service.list_growth(1036, limit=10, offset=10)
        first_combat = self.service.list_combat_links(1036, limit=500, offset=0)

        self.assertEqual(86, first_growth.total)
        self.assertEqual(10, len(first_growth.items))
        self.assertEqual(10, len(second_growth.items))
        self.assertNotEqual(first_growth.items[0], second_growth.items[0])
        self.assertGreater(first_combat.total, 0)
        self.assertTrue(any(link.ability_id == "GA_Zankou_Skill" for link in first_combat.items))

        owned_offset = max(0, first_combat.total - 100)
        owned_page = self.service.list_combat_links(
            1036,
            limit=100,
            offset=owned_offset,
        )
        self.assertTrue(any(
            link.relationship_kind == "character_owned_buff"
            for link in owned_page.items
        ))

    def test_transformation_keeps_missing_growth_explicit(self) -> None:
        detail = self.service.get_character_detail(1091)

        assert detail is not None
        self.assertEqual(0, detail.growth_count)
        gaps = {gap.field_key for gap in detail.gaps}
        self.assertIn("character_panel_growth", gaps)


if __name__ == "__main__":
    unittest.main()
