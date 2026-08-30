# 浸染正式公式已知，但未接入固定轴前不得伪造环合强度边际。
from __future__ import annotations

import unittest

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayFactor,
    BattleHitReplayResult,
)
from src.services.battle_hit_counterfactual_ratio_service import (
    BattleHitCounterfactualRatioService,
)


class BattleStainMarginalBoundaryTests(unittest.TestCase):
    def test_ring_strength_excludes_stain_until_per_hit_consumer_exists(self) -> None:
        hit = BattleAnalysisHit(
            event_id="stain:1",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=1072,
            character_name="灵可",
            skill_name="环合·浸染",
            damage_name="浸染",
            damage_component="reaction",
            attack_type="QTE",
            damage_attribute="nature",
            target_id="target:1",
            target_name="目标",
            damage=600.0,
            direction="outgoing",
            is_follow_up=False,
            classification="reaction",
        )
        replay = BattleHitReplayResult(
            event_id=hit.event_id,
            observed_damage=hit.damage,
            non_critical_damage=hit.damage,
            critical_damage=None,
            selected_damage=hit.damage,
            selected_error_percent=0.0,
            critical_state="not_applicable",
            confidence="低",
            factors=(BattleHitReplayFactor(
                factor_id="scaling",
                label="环合强度区",
                value=1.0,
                evidence_basis="官方公式已确认，生产逐击消费者尚未接入",
            ),),
            critical_policy="disabled",
        )

        supported = BattleHitCounterfactualRatioService.supports_ring_strength(
            hit,
            replay,
        )

        self.assertFalse(supported)


if __name__ == "__main__":
    unittest.main()
