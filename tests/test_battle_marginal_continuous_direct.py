# 验证持续直伤与安魂曲五觉在属性单位边际中使用同一来源倍率。
from __future__ import annotations

import unittest
from dataclasses import dataclass, replace
from types import SimpleNamespace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleMaxHpReductionEvent,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)


CHARACTER_ID = 1004


@dataclass
class _AnalysisFixture:
    baselines: tuple
    hits: tuple
    hit_replays: tuple
    buff_intervals: tuple
    roles: tuple
    effective_damage: float
    build_counterfactual: object | None
    target_condition: object | None
    target_conditions_by_half: tuple
    target_instance_resolutions: tuple
    target_instance_mapping_required: bool
    max_hp_events: tuple


def _baseline() -> BattleCharacterBaseline:
    values = {
        "AtkBase": 1000.0,
        "AtkUp": 0.0,
        "AtkAdd": 0.0,
        "CritBase": 0.5,
        "CritDamageBase": 1.0,
        "DamageUpGeneralBase": 0.0,
        "DamageUpChaosBase": 0.0,
        "DamageUpIncantationBase": 0.0,
    }
    return BattleCharacterBaseline(
        character_id=CHARACTER_ID,
        character_name="安魂曲",
        source="fixture",
        stats=tuple(
            BattleCharacterStat(key, key, value, key not in {"AtkBase", "AtkAdd"})
            for key, value in values.items()
        ),
    )


def _nightmare() -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="nightmare:1",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=CHARACTER_ID,
        character_name="安魂曲",
        skill_name="噩梦",
        damage_name="噩梦",
        damage_component="skill",
        attack_type="Special Damage",
        damage_attribute="chaos",
        target_id="target:1",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="dot",
        gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage_LV6",
    )


def _replay(hit: BattleAnalysisHit) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=hit.damage,
        non_critical_damage=None,
        critical_damage=None,
        selected_damage=None,
        selected_error_percent=None,
        critical_state="unreplayable",
        confidence="低",
        factors=(),
        critical_rate=None,
        critical_policy="unknown",
    )


def _vital(*, evidence_event_ids: tuple[str, ...]) -> BattleMaxHpReductionEvent:
    return BattleMaxHpReductionEvent(
        event_id="vital:1",
        observed_at_us=1_000_100,
        scope_half="",
        target_id="target:1",
        target_name="目标",
        old_max_hp=10_000.0,
        new_max_hp=8_000.0,
        max_hp_reduction=2_000.0,
        hp_before_settlement=4_000.0,
        hp_ratio_before=0.4,
        effective_hp_loss=800.0,
        source_character_id=CHARACTER_ID,
        source_character_name="安魂曲",
        mechanic_kind="lacrimosa_nightmare_awaken_5",
        mechanic_name="安魂曲五觉·噩梦生命上限削减",
        source_skill_name="噩梦",
        evidence_event_ids=evidence_event_ids,
        attribution_confidence="高",
        calculation_confidence="高",
        inference_basis="fixture",
    )


def _analysis(*, evidence_event_ids: tuple[str, ...] = ("nightmare:1",)):
    hit = _nightmare()
    vital = _vital(evidence_event_ids=evidence_event_ids)
    return _AnalysisFixture(
        baselines=(_baseline(),),
        hits=(hit,),
        hit_replays=(_replay(hit),),
        buff_intervals=(),
        roles=(SimpleNamespace(
            character_id=CHARACTER_ID,
            max_hp_reduction_damage=vital.effective_hp_loss,
        ),),
        effective_damage=hit.damage + vital.effective_hp_loss,
        build_counterfactual=None,
        target_condition=None,
        target_conditions_by_half=(),
        target_instance_resolutions=(),
        target_instance_mapping_required=False,
        max_hp_events=(vital,),
    )


def _scorch_analysis() -> _AnalysisFixture:
    hit = replace(
        _nightmare(),
        event_id="scorch:1",
        skill_name="浊燃",
        damage_name="浊燃",
        damage_attribute="incantation",
        classification="reaction",
        gameplay_effect_id="Buff_Reaction_5_new_1036",
    )
    return _AnalysisFixture(
        baselines=(_baseline(),),
        hits=(hit,),
        hit_replays=(_replay(hit),),
        buff_intervals=(),
        roles=(SimpleNamespace(
            character_id=CHARACTER_ID,
            max_hp_reduction_damage=0.0,
        ),),
        effective_damage=hit.damage,
        build_counterfactual=None,
        target_condition=None,
        target_conditions_by_half=(),
        target_instance_resolutions=(),
        target_instance_mapping_required=False,
        max_hp_events=(),
    )


class BattleMarginalContinuousDirectTests(unittest.TestCase):
    def test_scorch_only_consumes_crit_damage_of_three_requested_units(self) -> None:
        rows = BattleMarginalCalculationService.calculate(
            analysis=_scorch_analysis(),
            character_id=CHARACTER_ID,
            edited_values={},
            units={
                "CritBase": 0.01,
                "CritDamageBase": 0.02,
                "DamageUpIncantationBase": 0.1,
            },
        )
        by_property = {row.property_id: row for row in rows}

        self.assertEqual(
            "not_applicable",
            by_property["CritBase"].quantification.status,
        )
        self.assertEqual(
            "not_applicable",
            by_property["DamageUpIncantationBase"].quantification.status,
        )
        crit_damage = by_property["CritDamageBase"]
        self.assertEqual("complete", crit_damage.quantification.status)
        self.assertAlmostEqual(1.51 / 1.50 * 1000.0, crit_damage.known_projection_damage)

    def test_nightmare_crit_damage_scales_body_and_effect5_settlement(self) -> None:
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"CritDamageBase": 0.02},
        )[0]

        ratio = 1.51 / 1.50
        candidate_body = 1000.0 * ratio
        candidate_hp_before = 4000.0 - (candidate_body - 1000.0)
        candidate_reduction = 2000.0 * ratio
        candidate_vital = candidate_hp_before * candidate_reduction / 10_000.0
        candidate_total = candidate_body + candidate_vital
        self.assertEqual("complete", result.quantification.status)
        self.assertEqual(1800.0, result.quantification.fully_quantified_damage)
        self.assertAlmostEqual(candidate_total, result.known_projection_damage)
        self.assertAlmostEqual(
            (candidate_total / 1800.0 - 1.0) * 100.0,
            result.full_role_gain_percent,
        )

    def test_nightmare_chaos_scales_body_and_effect5_settlement(self) -> None:
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DamageUpChaosBase": 0.1},
        )[0]

        candidate_body = 1100.0
        candidate_hp_before = 4000.0 - (candidate_body - 1000.0)
        candidate_reduction = 2200.0
        candidate_vital = candidate_hp_before * candidate_reduction / 10_000.0
        candidate_total = candidate_body + candidate_vital
        self.assertEqual("complete", result.quantification.status)
        self.assertAlmostEqual(candidate_total, result.known_projection_damage)
        self.assertAlmostEqual(
            (candidate_total / 1800.0 - 1.0) * 100.0,
            result.full_role_gain_percent,
        )

    def test_nightmare_panel_crit_rate_is_not_applicable(self) -> None:
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"CritBase": 0.01},
        )[0]

        self.assertEqual("not_applicable", result.quantification.status)
        self.assertEqual(1800.0, result.known_projection_damage)
        self.assertEqual(0.0, result.full_role_gain_percent)

    def test_missing_effect5_source_is_unavailable_not_proven_unchanged(self) -> None:
        result = BattleMarginalCalculationService.calculate(
            analysis=_analysis(evidence_event_ids=()),
            character_id=CHARACTER_ID,
            edited_values={},
            units={"DamageUpChaosBase": 0.1},
        )[0]

        self.assertEqual("partial", result.quantification.status)
        self.assertEqual(1000.0, result.quantification.fully_quantified_damage)
        self.assertEqual(800.0, result.quantification.unavailable_damage)
        self.assertEqual(0.0, result.quantification.proven_unchanged_damage)
        self.assertIsNone(result.full_role_gain_percent)


if __name__ == "__main__":
    unittest.main()
