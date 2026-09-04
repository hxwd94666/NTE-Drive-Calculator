# 验证灵可会改变固定轴事件集合的觉醒差异不会被误报为完整零收益。
from __future__ import annotations

import unittest

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleRangeRoleSummary,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)


def _baseline(*, effects: tuple[str, ...] = (), crit: float = 0.25):
    return BattleCharacterBaseline(
        character_id=1072,
        character_name="灵可",
        source="fixture",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击力", 100.0, False),
            BattleCharacterStat("AtkUp", "攻击力提升", 0.0, True),
            BattleCharacterStat("AtkAdd", "固定攻击力", 0.0, False),
            BattleCharacterStat("CritBase", "暴击率", crit, True),
            BattleCharacterStat("CritDamageBase", "暴击伤害", 1.0, True),
            BattleCharacterStat("DamageUpGeneralBase", "通伤", 0.0, True),
            BattleCharacterStat("DamageUpNatureBase", "灵伤", 0.0, True),
        ),
        selected_awaken_effect_ids=effects,
    )


def _snapshot(baseline: BattleCharacterBaseline) -> BattleAnalysisSnapshot:
    crit_rate = next(
        row.value for row in baseline.stats if row.property_id == "CritBase"
    )
    expected_damage = 100.0 * (1.0 + crit_rate)
    hit = BattleAnalysisHit(
        event_id="hit1",
        sequence=1,
        relative_time_us=1_000_000,
        character_id=1072,
        character_name="灵可",
        skill_name="普通攻击",
        damage_name="第一段",
        damage_component="direct",
        attack_type="normal",
        damage_attribute="nature",
        target_id="target",
        target_name="目标",
        damage=125.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )
    replay = BattleHitReplayResult(
        event_id=hit.event_id,
        observed_damage=125.0,
        non_critical_damage=100.0,
        critical_damage=200.0,
        selected_damage=None,
        selected_error_percent=None,
        critical_state="ambiguous",
        confidence="高",
        factors=(),
        expected_damage=expected_damage,
        critical_rate=crit_rate,
        critical_policy="character",
        formula_damage_attribute="nature",
        formula_panel_character_id=1072,
    )
    role = BattleRangeRoleSummary(
        character_id=1072,
        character_name="灵可",
        hits=1,
        damage=125.0,
        dps=12.5,
        share_percent=100.0,
    )
    return BattleAnalysisSnapshot(
        battle_record_id=62,
        capability_level="formal_hit",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=10_000_000,
        timeline_end_us=10_000_000,
        range_start_us=0,
        range_end_us=10_000_000,
        duration_seconds=10.0,
        total_damage=125.0,
        total_dps=12.5,
        timeline_hits=(hit,),
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=(hit,),
        roles=(role,),
        skills=(),
        targets=(),
        baselines=(baseline,),
        effective_damage=125.0,
        effective_dps=12.5,
        hit_replays=(replay,),
    )


class BattleLinkoAwakeningCounterfactualGapTests(unittest.TestCase):
    def test_ungenerated_awakenings_are_not_complete_zero_gain(self) -> None:
        original = _snapshot(_baseline())
        for effect_id in ("Effect1", "Effect3", "Effect4", "Effect5"):
            with self.subTest(effect_id=effect_id):
                candidate = _snapshot(_baseline(effects=(effect_id,)))
                result = BattleBuildCounterfactualService.compare(
                    original=original,
                    candidate=candidate,
                )

                self.assertEqual("partial", result.quantification.status)
                self.assertIsNone(result.candidate_damage)
                self.assertEqual(125.0, result.known_projection_damage)
                self.assertTrue(any(
                    effect_id.casefold() in gap.dimension_id.casefold()
                    for gap in result.quantification.gaps
                ))

    def test_effect_six_keeps_known_crit_gain_but_adds_resource_gap(self) -> None:
        original = _snapshot(_baseline())
        candidate = _snapshot(_baseline(effects=("Effect6",), crit=0.50))

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual("partial", result.quantification.status)
        self.assertGreater(result.known_projection_damage or 0.0, 125.0)
        self.assertIsNone(result.candidate_damage)
        self.assertEqual("partial", result.roles[0].quantification.status)
        self.assertGreater(result.roles[0].known_projection_damage or 0.0, 125.0)
        self.assertIn(
            "linko_effect6_resource_restore_unquantified",
            {gap.code for gap in result.quantification.gaps},
        )


if __name__ == "__main__":
    unittest.main()
