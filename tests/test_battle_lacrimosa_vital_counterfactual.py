# 验证安魂曲五觉以实测生命结算为锚，不从残缺血量轴制造负收益。
from __future__ import annotations

import unittest
from types import SimpleNamespace

from src.domain.battle_counterfactual_quantification import (
    BattleCounterfactualRatio,
)
from src.services.battle_buff_counterfactual_projection_support import (
    HitProjection,
    vital_projections,
)


def _complete_ratio(ratio: float) -> BattleCounterfactualRatio:
    return BattleCounterfactualRatio.complete(
        ratio,
        method="fixture",
        confidence="高",
        dependency_scope="character_only",
        explanation="fixture",
    )


def _hit(event_id: str, damage: float, *, time_us: int, effect: str) -> object:
    return SimpleNamespace(
        event_id=event_id,
        relative_time_us=time_us,
        character_id=1004,
        target_id="target",
        scope_half="",
        direction="outgoing",
        gameplay_effect_id=effect,
        damage=damage,
    )


class BattleLacrimosaVitalCounterfactualTests(unittest.TestCase):
    def test_positive_hit_changes_scale_observed_settlement_without_hp_rebuild(
        self,
    ) -> None:
        prior = _hit("prior", 1_000.0, time_us=100, effect="ordinary")
        nightmare = _hit(
            "nightmare",
            100.0,
            time_us=200,
            effect="GE_Player_Lacrimosa_Blood_Damage",
        )
        event = SimpleNamespace(
            event_id="vital",
            observed_at_us=300,
            old_max_hp=1_000.0,
            hp_before_settlement=500.0,
            max_hp_reduction=600.0,
            effective_hp_loss=300.0,
            source_character_id=1004,
            mechanic_kind="lacrimosa_nightmare_awaken_5",
            evidence_event_ids=("nightmare",),
            target_id="target",
            scope_half="",
        )
        projections = {
            "prior": HitProjection(prior, 1_300.0, _complete_ratio(1.3)),
            "nightmare": HitProjection(
                nightmare,
                120.0,
                _complete_ratio(1.2),
            ),
        }

        (result,) = vital_projections(
            SimpleNamespace(hits=(prior, nightmare), max_hp_events=(event,)),
            projections,
            {"prior": 1_000.0, "nightmare": 100.0},
        )

        self.assertEqual("complete", result.status)
        self.assertEqual(300.0, result.baseline_damage)
        self.assertEqual(360.0, result.predicted_damage)

    def test_candidate_estimate_remains_in_followup_attribute_marginal(self) -> None:
        nightmare = _hit(
            "nightmare",
            100.0,
            time_us=200,
            effect="GE_Player_Lacrimosa_Blood_Damage",
        )
        estimate = SimpleNamespace(
            event_id="estimate",
            observed_at_us=200,
            old_max_hp=0.0,
            hp_before_settlement=0.0,
            max_hp_reduction=200.0,
            effective_hp_loss=100.0,
            source_character_id=1004,
            mechanic_kind="lacrimosa_nightmare_awaken_5_estimated",
            evidence_event_ids=("nightmare",),
            target_id="target",
            scope_half="",
        )

        (result,) = vital_projections(
            SimpleNamespace(
                hits=(nightmare,),
                max_hp_events=(),
                estimated_max_hp_events=(estimate,),
            ),
            {
                "nightmare": HitProjection(
                    nightmare,
                    108.0,
                    _complete_ratio(1.08),
                ),
            },
            {"nightmare": 100.0},
            {"estimate": (0.0, 0.0, 200.0, 100.0)},
        )

        self.assertEqual(100.0, result.baseline_damage)
        self.assertEqual(108.0, result.predicted_damage)


if __name__ == "__main__":
    unittest.main()
