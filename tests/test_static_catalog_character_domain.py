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
            self.assertEqual(30, page.dataset.schema_version)

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
        self.assertNotIn("material_item_names", gaps)

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
        self.assertEqual(
            ("暮落残阳", "殷红幻景"),
            tuple(passive.name_zh for passive in detail.passives),
        )
        self.assertEqual((2, 4), tuple(
            passive.unlock_stage for passive in detail.passives
        ))
        self.assertTrue(all(passive.descriptions for passive in detail.passives))

        protagonist = self.service.get_character_detail(1051)
        assert protagonist is not None
        self.assertEqual(
            ("鉴定师", "异象感知力"),
            tuple(passive.name_zh for passive in protagonist.passives),
        )

    def test_graduation_and_cultivation_keep_formal_fork_association(self) -> None:
        detail = self.service.get_character_detail(1036)

        assert detail is not None
        self.assertIsNotNone(detail.cultivation)
        self.assertIsNotNone(detail.graduation)
        assert detail.graduation is not None
        self.assertEqual("fork_DemonBlade", detail.graduation.fork_id)
        self.assertEqual("噬心诡刃", detail.graduation.fork_name_zh)
        self.assertTrue(any("fork_DemonBlade" in path for path in detail.graduation.fork_paths))

    def test_detail_projects_formal_blueprint_shape_and_project_weights(self) -> None:
        detail = self.service.get_character_detail(1036)

        assert detail is not None
        assert detail.equipment_plan is not None
        self.assertEqual(25, len(detail.equipment_plan.cells))
        self.assertEqual(20, sum(
            module_ordinal is not None
            for _row, _column, module_ordinal in detail.equipment_plan.cells
        ))
        self.assertEqual(7, len(detail.equipment_plan.modules))
        self.assertTrue(detail.equipment_plan.core_attributes)
        self.assertTrue(detail.equipment_plan.recommended_attributes)
        assert detail.shape_bonus is not None
        self.assertGreater(detail.shape_bonus.shape_grid_count, 0)
        self.assertTrue(detail.shape_bonus.properties)
        assert detail.recommended_weights is not None
        self.assertTrue(detail.recommended_weights.properties)

    def test_detail_projects_graduation_stats_damage_items_and_level_hints(self) -> None:
        detail = self.service.get_character_detail(1036)

        assert detail is not None
        assert detail.graduation is not None
        self.assertTrue(detail.graduation.core_main_stats)
        self.assertTrue(detail.graduation.drive_template_stats)
        melee = next(
            skill for skill in detail.skills
            if skill.skill_id == "GA_Zankou_Melee"
        )
        self.assertEqual(32, len(melee.damage_items))
        self.assertEqual(21, len(melee.level_hints))
        self.assertTrue(all(item.damage_id for item in melee.damage_items))
        self.assertTrue(any(item.atk_rates for item in melee.damage_items))
        self.assertTrue(all(
            all(value >= 0 for value in item.atk_rates)
            for item in melee.damage_items
        ))

    def test_v30_character_value_coverage_counts_are_preserved(self) -> None:
        characters = tuple(
            item for item in self.service.list_characters(limit=200).items
            if item.classification != "combat_transformation"
        )
        details = tuple(
            self.service.get_character_detail(item.character_id)
            for item in characters
        )

        self.assertEqual(23, len(details))
        self.assertEqual(23, sum(
            detail is not None and detail.equipment_plan is not None
            for detail in details
        ))
        self.assertTrue(all(
            detail is not None
            and detail.equipment_plan is not None
            and len(detail.equipment_plan.cells) == 25
            and sum(
                module is not None
                for _row, _column, module in detail.equipment_plan.cells
            ) == 20
            for detail in details
        ))
        self.assertEqual(23, sum(
            detail is not None and detail.shape_bonus is not None
            for detail in details
        ))
        self.assertEqual(23, sum(
            detail is not None and detail.recommended_weights is not None
            for detail in details
        ))
        self.assertEqual(23, sum(
            detail is not None and detail.cultivation is not None
            for detail in details
        ))
        self.assertEqual(22, sum(
            detail is not None and detail.graduation is not None
            for detail in details
        ))
        skills = tuple(
            skill
            for detail in details if detail is not None
            for skill in detail.skills
        )
        self.assertEqual(92, len(skills))
        self.assertEqual(645, sum(len(skill.damage_items) for skill in skills))
        self.assertEqual(626, sum(len(skill.level_hints) for skill in skills))

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
