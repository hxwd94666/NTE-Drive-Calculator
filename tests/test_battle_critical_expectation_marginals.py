# 验证暴击收益按实测逐击加权，并逐击处理动态加成、上限与特殊策略。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_counterfactual_formula_support import critical_ratio
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from tests.test_battle_marginal_calculation_service import (
    CHARACTER_ID,
    _analysis,
    _critical_replay,
    _hit,
)


class BattleCriticalExpectationMarginalsTests(unittest.TestCase):
    def test_resolved_hits_receive_theoretical_gain_weighted_by_observed_damage(self):
        first = _hit(event_id="first", damage=2000.0)
        second = replace(_hit(event_id="second", damage=300.0), sequence=2)
        replays = tuple(
            replace(_critical_replay(hit, "character", 0.5), critical_state=state)
            for hit, state in ((first, "critical"), (second, "non_critical"))
        )
        analysis = _analysis(first, replays[0])
        analysis.hits = (first, second)
        analysis.hit_replays = replays
        analysis.effective_damage = 2300.0

        for property_id, unit in (("CritBase", 0.01), ("CritDamageBase", 0.02)):
            with self.subTest(property_id=property_id):
                row = BattleMarginalCalculationService.calculate(
                    analysis=analysis, character_id=CHARACTER_ID,
                    edited_values={}, units={property_id: unit},
                )[0]
                # Both changes raise the fixture's expectation from 1.5 to 1.51.
                self.assertAlmostEqual(2300.0 * 1.51 / 1.5, row.known_projection_damage)
                self.assertAlmostEqual(100.0 * 0.01 / 1.5, row.full_role_gain_percent)
                self.assertEqual("complete", row.quantification.status)
                self.assertEqual(2000.0, first.damage)
                self.assertEqual(300.0, second.damage)

    def test_only_hits_below_cap_gain_rate_after_their_dynamic_buffs(self):
        first = _hit(event_id="buffed", damage=2000.0)
        second = replace(
            _hit(event_id="unbuffed", damage=1000.0),
            sequence=2, relative_time_us=3_000_000,
        )
        analysis = _analysis(first, _critical_replay(first, "character", 1.0))
        analysis.hits = (first, second)
        analysis.hit_replays = tuple(
            replace(_critical_replay(hit, "character", rate), critical_state="critical")
            for hit, rate in ((first, 1.0), (second, 0.5))
        )
        analysis.effective_damage = 3000.0
        analysis.buff_intervals = (BattleInferredBuffInterval(
            interval_id="crit", buff_asset_path="/Game/Test/Buff_Crit",
            buff_name="暴击率提升", source_effect_definition_id="fixture:crit",
            source_kind="fixture", source_character_id=CHARACTER_ID,
            source_character_name="灵可", target_scope="self",
            start_us=0, end_us=2_000_000, stacks=1, duration_policy="HasDuration",
            state_confidence="高", value_confidence="高", inference_basis="fixture",
            trigger_event_type="STATIC_EQUIPPED_SOURCE",
            evidence_action_ids=(), evidence_event_ids=(),
            modifiers=(BattleBuffModifierEvidence(
                property_id="CritBase", modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="constant", magnitude_value=0.5,
                calculation_asset_path="", value_confidence="高",
            ),),
        ),)

        row = BattleMarginalCalculationService.calculate(
            analysis=analysis, character_id=CHARACTER_ID,
            edited_values={}, units={"CritBase": 0.1},
        )[0]
        self.assertAlmostEqual(2000.0 + 1000.0 * 1.6 / 1.5, row.known_projection_damage)

    def test_branch_labels_do_not_change_component_expectation(self):
        original = {"CritBase": 0.53, "CritDamageBase": 2.34}
        for state in ("critical", "non_critical", "ambiguous", "unreplayable"):
            replay = replace(_critical_replay(_hit(), "character", 0.53), critical_state=state)
            with self.subTest(state=state):
                self.assertAlmostEqual(
                    (1.0 + 0.54 * 2.34) / (1.0 + 0.53 * 2.34),
                    critical_ratio(original, {**original, "CritBase": 0.54}, replay),
                )
                self.assertAlmostEqual(
                    (1.0 + 0.53 * 2.36) / (1.0 + 0.53 * 2.34),
                    critical_ratio(original, {**original, "CritDamageBase": 2.36}, replay),
                )

    def test_cap_crossing_and_simultaneous_changes_use_one_ratio(self):
        replay = _critical_replay(_hit(), "character", 0.99)
        self.assertAlmostEqual(
            2.2 / 1.99,
            critical_ratio(
                {"CritBase": 0.99, "CritDamageBase": 1.0},
                {"CritBase": 1.09, "CritDamageBase": 1.2}, replay,
            ),
        )

    def test_fixed_disabled_and_unknown_policies_do_not_invent_rate_gain(self):
        original = {"CritBase": 0.5, "CritDamageBase": 1.0}
        candidate = {"CritBase": 0.8, "CritDamageBase": 1.2}
        for state in ("critical", "non_critical", "ambiguous"):
            for policy, rate, expected in (
                ("fixed", 0.5, 1.6 / 1.5), ("fixed", 0.0, 1.0),
                ("disabled", 0.0, 1.0), ("fixed", None, None),
                ("unknown", None, None),
            ):
                with self.subTest(state=state, policy=policy, rate=rate):
                    replay = replace(_critical_replay(_hit(), policy, rate), critical_state=state)
                    ratio = critical_ratio(original, candidate, replay)
                    if expected is None:
                        self.assertIsNone(ratio)
                    else:
                        self.assertAlmostEqual(expected, ratio)
            for channel in (
                "special_nightmare", "special_zankou_erosion",
                "special_zankou_venom", "reaction_scorch",
            ):
                replay = replace(_critical_replay(_hit(), "fixed", 0.5), critical_state=state)
                self.assertAlmostEqual(
                    1.6 / 1.5, critical_ratio(original, candidate, replay, channel_id=channel),
                )
