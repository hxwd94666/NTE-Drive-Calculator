# 测试统一养成体力计算的等级、缺口和最低体力语义。
from __future__ import annotations

import unittest

from src.domain.progression_stamina import (
    FarmingStage,
    MaterialRequirement,
    MaterialYield,
    ProgressionStaminaRequest,
    StaminaPlanStatus,
)
from src.services.progression_stamina_service import ProgressionStaminaService


def _stage(
    stage_id: str,
    stamina: int,
    yields: tuple[tuple[str, int], ...],
    *,
    hunter_level: int = 1,
    identification_level: int = 0,
) -> FarmingStage:
    return FarmingStage(
        stage_id=stage_id,
        label=stage_id,
        minimum_hunter_level=hunter_level,
        minimum_identification_level=identification_level,
        stamina_cost=stamina,
        yields=tuple(MaterialYield(item_id, quantity) for item_id, quantity in yields),
        source="test_fixture",
    )


class ProgressionStaminaServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ProgressionStaminaService()

    def test_hunter_level_uses_the_formal_identification_thresholds(self) -> None:
        expected = {
            1: 0,
            9: 0,
            10: 1,
            19: 1,
            20: 2,
            30: 3,
            40: 4,
            45: 5,
            50: 6,
            55: 7,
            60: 7,
        }
        for hunter_level, identification_level in expected.items():
            with self.subTest(hunter_level=hunter_level):
                self.assertEqual(
                    self.service.identification_level(hunter_level).native_level,
                    identification_level,
                )

    def test_identification_adjustment_allows_only_one_level_after_unlock(self) -> None:
        lowered = self.service.identification_level(40, effective_level=3)
        self.assertEqual(lowered.native_level, 4)
        self.assertEqual(lowered.effective_level, 3)
        self.assertTrue(lowered.lowered)
        with self.assertRaisesRegex(ValueError, "只能保持不变"):
            self.service.identification_level(20, effective_level=1)
        with self.assertRaisesRegex(ValueError, "下调一级"):
            self.service.identification_level(40, effective_level=2)

    def test_owned_materials_are_removed_before_exact_stamina_planning(self) -> None:
        result = self.service.calculate(ProgressionStaminaRequest(
            hunter_level=50,
            requirements=(MaterialRequirement("skill_lv3", 5, 1),),
            stages=(_stage("talent_7", 40, (("skill_lv3", 1),)),),
        ))
        self.assertEqual(result.status, StaminaPlanStatus.COMPLETE)
        self.assertEqual(result.deficits[0].deficit_quantity, 4)
        self.assertEqual(result.runs[0].runs, 4)
        self.assertEqual(result.total_stamina, 160)

    def test_planner_uses_multi_material_bundles_without_double_counting_runs(self) -> None:
        result = self.service.calculate(ProgressionStaminaRequest(
            hunter_level=50,
            requirements=(
                MaterialRequirement("high", 3),
                MaterialRequirement("low", 3),
            ),
            stages=(
                _stage("bundle", 40, (("high", 2), ("low", 1))),
                _stage("low_only", 30, (("low", 2),)),
                _stage("high_only", 30, (("high", 1),)),
            ),
        ))
        self.assertEqual(result.status, StaminaPlanStatus.COMPLETE)
        self.assertEqual(result.total_stamina, 100)
        self.assertEqual(
            {run.stage_id: run.runs for run in result.runs},
            {"bundle": 1, "high_only": 1, "low_only": 1},
        )

    def test_effective_identification_level_filters_locked_stages(self) -> None:
        result = self.service.calculate(ProgressionStaminaRequest(
            hunter_level=45,
            effective_identification_level=4,
            requirements=(MaterialRequirement("material", 4),),
            stages=(
                _stage(
                    "level_5",
                    40,
                    (("material", 4),),
                    identification_level=5,
                ),
                _stage(
                    "level_4",
                    40,
                    (("material", 2),),
                    identification_level=4,
                ),
            ),
        ))
        self.assertEqual(result.identification.native_level, 5)
        self.assertEqual(result.identification.effective_level, 4)
        self.assertEqual(result.total_stamina, 80)
        self.assertEqual(result.runs[0].stage_id, "level_4")

    def test_missing_drop_yield_keeps_known_stamina_but_not_a_fake_total(self) -> None:
        result = self.service.calculate(ProgressionStaminaRequest(
            hunter_level=50,
            requirements=(
                MaterialRequirement("known", 4),
                MaterialRequirement("unknown", 2),
            ),
            stages=(_stage("known_stage", 40, (("known", 2),)),),
        ))
        self.assertEqual(result.status, StaminaPlanStatus.PARTIAL)
        self.assertEqual(result.known_stamina, 80)
        self.assertIsNone(result.total_stamina)
        self.assertEqual(result.unresolved_item_ids, ("unknown",))
        self.assertEqual(result.gaps, ("material_yield_unavailable",))

    def test_no_deficit_is_a_complete_zero_stamina_result(self) -> None:
        result = self.service.calculate(ProgressionStaminaRequest(
            hunter_level=10,
            requirements=(MaterialRequirement("done", 3, 5),),
            stages=(),
        ))
        self.assertEqual(result.status, StaminaPlanStatus.COMPLETE)
        self.assertEqual(result.known_stamina, 0)
        self.assertEqual(result.total_stamina, 0)
        self.assertEqual(result.runs, ())

    def test_service_rejects_a_non_positive_search_guard(self) -> None:
        with self.assertRaisesRegex(ValueError, "搜索上限"):
            ProgressionStaminaService(maximum_search_states=0)


if __name__ == "__main__":
    unittest.main()
