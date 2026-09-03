# 验证角色等级材料汇总不依赖 Qt 或活力计算服务。
from __future__ import annotations

import unittest

from src.services.character_progression_requirements import (
    project_character_level_requirements,
)
from src.services.static_catalog_character_models import (
    CatalogSource,
    CharacterBreakthroughRequirement,
    CharacterExperienceMaterial,
    CharacterMaterialCost,
    CharacterProgressionProfile,
    CharacterUpgradeLevel,
)


NTE_TEST_TIER = "core"


_SOURCE = CatalogSource(table_name="formal_character_progression")


def _experience_materials() -> tuple[CharacterExperienceMaterial, ...]:
    return (
        CharacterExperienceMaterial(
            item_id="CharacterUpMaterial_lv1",
            experience_value=1_000,
            costs=(CharacterMaterialCost("Fons", 250),),
            source=_SOURCE,
        ),
        CharacterExperienceMaterial(
            item_id="CharacterUpMaterial_lv2",
            experience_value=5_000,
            costs=(CharacterMaterialCost("Fons", 1_250),),
            source=_SOURCE,
        ),
        CharacterExperienceMaterial(
            item_id="CharacterUpMaterial_lv3",
            experience_value=20_000,
            costs=(CharacterMaterialCost("Fons", 5_000),),
            source=_SOURCE,
        ),
    )


def _profile(
    upgrade_levels: tuple[CharacterUpgradeLevel, ...],
    *,
    stages: tuple[CharacterBreakthroughRequirement, ...] = (),
) -> CharacterProgressionProfile:
    return CharacterProgressionProfile(
        character_id=1075,
        upgrade_pack_id="character_Upgrade",
        breakthrough_pack_id="oneiroi_Breakthrough",
        upgrade_levels=upgrade_levels,
        breakthrough_stages=stages,
        experience_materials=_experience_materials(),
        source=_SOURCE,
    )


class CharacterProgressionProjectionTests(unittest.TestCase):
    def test_experience_books_minimize_overflow_then_book_count(self) -> None:
        projection = project_character_level_requirements(
            _profile((CharacterUpgradeLevel(1, 6_169_080, _SOURCE),)),
            from_level=1,
            to_level=2,
            include_breakthroughs=False,
        )

        self.assertEqual("complete", projection.status.value)
        self.assertEqual(6_169_080, projection.required_experience)
        self.assertEqual(920, projection.experience_overflow)
        self.assertEqual(
            {
                "CharacterUpMaterial_lv2": 2,
                "CharacterUpMaterial_lv3": 308,
            },
            {
                item.item_id: item.required_quantity
                for item in projection.experience_books
            },
        )
        self.assertEqual(
            {"Fons": 1_542_500},
            {
                item.item_id: item.required_quantity
                for item in projection.additional_costs
            },
        )

    def test_optional_breakthroughs_merge_formal_materials_and_fons(self) -> None:
        levels = tuple(
            CharacterUpgradeLevel(level, 20 if level == 1 else 1, _SOURCE)
            for level in range(1, 21)
        )
        stages = (
            CharacterBreakthroughRequirement(0, 20, 0, (), _SOURCE),
            CharacterBreakthroughRequirement(
                1,
                30,
                1,
                (
                    CharacterMaterialCost("OrdinaryMonMaterial_02_lv1", 3),
                    CharacterMaterialCost("Fons", 50_000),
                ),
                _SOURCE,
            ),
        )

        projection = project_character_level_requirements(
            _profile(levels, stages=stages),
            from_level=1,
            to_level=21,
            include_breakthroughs=True,
        )

        self.assertEqual((1,), projection.included_breakthrough_stages)
        self.assertEqual(
            {"OrdinaryMonMaterial_02_lv1": 3},
            {
                item.item_id: item.required_quantity
                for item in projection.breakthrough_materials
            },
        )
        self.assertEqual(
            {"Fons": 50_250},
            {
                item.item_id: item.required_quantity
                for item in projection.additional_costs
            },
        )


if __name__ == "__main__":
    unittest.main()
