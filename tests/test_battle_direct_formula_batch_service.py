# 验证无状态直伤只在公式上下文完全一致时归并。
from __future__ import annotations

from concurrent.futures import CancelledError
import unittest

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
)
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitBuffProjection,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_direct_formula_batch_service import (
    BattleDirectFormulaBatchService,
)


def _hit(event_id: str, damage: float, sequence: int) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=sequence,
        relative_time_us=sequence * 100_000,
        character_id=1,
        character_name="角色1",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="direct",
        attack_type="skill",
        damage_attribute="chaos",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Test",
        gameplay_effect_id="GE_Test",
    )


def _projection(event_id: str) -> BattleHitBuffProjection:
    return BattleHitBuffProjection(
        event_id=event_id,
        modifiers=(),
        applied_interval_ids=(),
        excluded_interval_ids=(),
        exclusion_reasons=(),
        confidence="中",
    )


def _evidence(
    event_id: str,
    *,
    scaling_multiplier: float = 1.0,
) -> BattleSkillDamageEvidence:
    return BattleSkillDamageEvidence(
        event_id=event_id,
        damage_id="GE_Test",
        ability_id="GA_Test",
        damage_attribute="chaos",
        damage_source_category="skill",
        fixed_crit_rate=0.0,
        scaling_property_id="Atk",
        scaling_multiplier=scaling_multiplier,
        multiplier_coefficient=1.0,
        effective_skill_level=10,
        evidence_basis="fixture",
        source_character_id=1,
        critical_policy="disabled",
    )


def _replay(event_id: str, damage: float = 100.0) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=event_id,
        observed_damage=damage,
        non_critical_damage=damage,
        critical_damage=None,
        selected_damage=damage,
        selected_error_percent=0.0,
        critical_state="not_applicable",
        confidence="高",
        factors=(),
        expected_damage=damage,
        critical_policy="disabled",
    )


class BattleDirectFormulaBatchServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hits = (_hit("a", 100.0, 1), _hit("b", 260.0, 2))
        self.baseline = BattleCharacterBaseline(
            character_id=1,
            character_name="角色1",
            source="fixture",
            stats=(
                BattleCharacterStat("AtkBase", "攻击", 1000.0, False),
                BattleCharacterStat(
                    "DamageUpGeneralBase",
                    "伤害提升",
                    0.25,
                    True,
                ),
            ),
            character_level=80.0,
        )
        self.condition = BattleTargetCondition(
            target_name="目标",
            enemy_level=80.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("chaos", 0.20),),
        )

    def _plan(
        self,
        *,
        second_evidence: BattleSkillDamageEvidence | None = None,
        second_replay: BattleHitReplayResult | None = None,
    ):
        projections = {
            hit.event_id: _projection(hit.event_id) for hit in self.hits
        }
        return BattleDirectFormulaBatchService.plan(
            self.hits,
            formula_character_id_by_event={"a": 1, "b": 1},
            baselines={1: self.baseline},
            evidence_by_event={
                "a": _evidence("a"),
                "b": second_evidence or _evidence("b"),
            },
            original_replay_by_event={
                "a": _replay("a"),
                "b": second_replay or _replay("b"),
            },
            original_projection_by_event=projections,
            candidate_projection_by_event=projections,
            candidate_formula_projection_by_event=projections,
            target_condition_by_event={
                "a": self.condition,
                "b": self.condition,
            },
        )

    def test_different_observed_damage_shares_one_formula_batch(self) -> None:
        batches = self._plan()

        self.assertEqual(1, len(batches))
        self.assertEqual(("a", "b"), tuple(
            hit.event_id for hit in batches[0].members
        ))

    def test_skill_multiplier_difference_splits_batches(self) -> None:
        batches = self._plan(
            second_evidence=_evidence("b", scaling_multiplier=1.0001),
        )

        self.assertEqual(2, len(batches))

    def test_original_branch_formula_difference_splits_batches(self) -> None:
        batches = self._plan(second_replay=_replay("b", 101.0))

        self.assertEqual(2, len(batches))

    def test_only_structured_complete_ratio_can_be_shared(self) -> None:
        structured = BattleCounterfactualRatio.complete(
            0.8,
            method="structured_selected",
            confidence="高",
            dependency_scope="target_sensitive",
            included_dimension_ids=("structured_formula",),
            explanation="fixture",
        )
        component = BattleCounterfactualRatio.complete(
            0.8,
            method="component_ratio",
            confidence="中",
            dependency_scope="character_only",
            included_dimension_ids=("scaling",),
            explanation="fixture",
        )

        self.assertTrue(
            BattleDirectFormulaBatchService.ratio_can_be_shared(structured)
        )
        self.assertFalse(
            BattleDirectFormulaBatchService.ratio_can_be_shared(component)
        )

    def test_large_batch_plan_can_cancel_at_throttled_checkpoint(self) -> None:
        hits = tuple(_hit(str(index), 100.0, index) for index in range(1, 66))
        projections = {hit.event_id: _projection(hit.event_id) for hit in hits}
        evidence = {hit.event_id: _evidence(hit.event_id) for hit in hits}
        replays = {hit.event_id: _replay(hit.event_id) for hit in hits}
        callbacks = 0

        def cancel(_progress) -> None:
            nonlocal callbacks
            callbacks += 1
            if callbacks == 2:
                raise CancelledError

        with self.assertRaises(CancelledError):
            BattleDirectFormulaBatchService.plan(
                hits,
                formula_character_id_by_event={hit.event_id: 1 for hit in hits},
                baselines={1: self.baseline},
                evidence_by_event=evidence,
                original_replay_by_event=replays,
                original_projection_by_event=projections,
                candidate_projection_by_event=projections,
                candidate_formula_projection_by_event=projections,
                target_condition_by_event={
                    hit.event_id: self.condition for hit in hits
                },
                progress_callback=cancel,
            )

        self.assertEqual(2, callbacks)


if __name__ == "__main__":
    unittest.main()
