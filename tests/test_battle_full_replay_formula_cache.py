# 验证主全轴重放只计算严格相同的无状态直伤公式一次。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_hit_replay_service import BattleHitReplayService


class BattleFullReplayFormulaCacheTests(unittest.TestCase):
    def test_identical_direct_formula_is_calculated_once_and_reanchored(self) -> None:
        first = BattleAnalysisHit(
            event_id="direct:1",
            sequence=1,
            relative_time_us=1,
            character_id=1,
            character_name="角色",
            skill_name="技能",
            damage_name="伤害",
            damage_component="skill",
            attack_type="E技能",
            damage_attribute="chaos",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            ability_id="GA_Test",
            gameplay_effect_id="GE_Test",
            scope_half="upper",
        )
        second = replace(first, event_id="direct:2", sequence=2, damage=200.0)
        baseline = BattleCharacterBaseline(
            character_id=1,
            character_name="角色",
            source="fixture",
            stats=(BattleCharacterStat("Atk", "攻击力", 100.0, False),),
        )
        evidence = BattleSkillDamageEvidence(
            event_id=first.event_id,
            damage_id="GE_Test",
            ability_id="GA_Test",
            damage_attribute="chaos",
            damage_source_category="skill",
            fixed_crit_rate=0.5,
            scaling_property_id="Atk",
            scaling_multiplier=1.0,
            multiplier_coefficient=1.0,
            effective_skill_level=1,
            evidence_basis="fixture",
        )
        condition = BattleTargetCondition(
            target_name="目标",
            enemy_level=80.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("chaos", 0.2),),
            enemy_defense_base=0.0,
        )
        analysis = SimpleNamespace(
            hits=(first, second),
            baselines=(baseline,),
            buff_intervals=(),
            target_condition=condition,
            target_instance_resolutions=(),
        )
        template = BattleHitReplayResult(
            event_id=first.event_id,
            observed_damage=100.0,
            non_critical_damage=100.0,
            critical_damage=200.0,
            selected_damage=100.0,
            selected_error_percent=0.0,
            critical_state="non_critical",
            confidence="高",
            factors=(),
            critical_rate=0.5,
            expected_damage=150.0,
            corrected_expected_damage=150.0,
            signed_error_percent=0.0,
            critical_policy="character",
        )

        with patch.object(
            BattleHitReplayService,
            "_replay_direct",
            return_value=template,
        ) as replay_direct:
            results = BattleHitReplayService.replay(
                analysis,
                (evidence, replace(evidence, event_id=second.event_id)),
                apply_observed_refinements=False,
            )

        replay_direct.assert_called_once()
        self.assertEqual(["direct:1", "direct:2"], [row.event_id for row in results])
        self.assertEqual(
            ["non_critical", "critical"],
            [row.critical_state for row in results],
        )
        self.assertEqual([100.0, 200.0], [row.observed_damage for row in results])


if __name__ == "__main__":
    unittest.main()
