# 验证选定时段逐 Buff 移除反事实与生命上限结算联动。
from __future__ import annotations

import unittest
from unittest.mock import patch

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleBuffModifierEvidence,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleMaxHpReductionEvent,
)
from src.services.battle_buff_counterfactual_service import (
    BattleBuffCounterfactualService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService


def _hit(event_id: str, damage: float, *, character_id: int = 1) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=1_000_000,
        character_id=character_id,
        character_name=f"角色{character_id}",
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
    )


def _replay(event_id: str, expected_damage: float) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=event_id,
        observed_damage=expected_damage,
        non_critical_damage=expected_damage,
        critical_damage=None,
        selected_damage=expected_damage,
        selected_error_percent=0.0,
        critical_state="non_critical",
        confidence="中",
        factors=(),
        expected_damage=expected_damage,
    )


def _modifier(value: float | None) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id="DamageUpGeneralBase",
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="ScalableFloat",
        magnitude_value=value,
        calculation_asset_path="",
        value_confidence="中" if value is not None else "低",
    )


def _interval(
    interval_id: str,
    *,
    start_us: int = 0,
    end_us: int = 10_000_000,
    target_scope: str = "self",
    modifiers: tuple[BattleBuffModifierEvidence, ...] = (),
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path="/Game/Buff_Test",
        buff_name="测试通伤 Buff",
        source_effect_definition_id="equipment:test:4",
        source_kind="equipment_suit",
        source_character_id=1,
        source_character_name="角色1",
        target_scope=target_scope,
        start_us=start_us,
        end_us=end_us,
        stacks=1,
        duration_policy="HasDuration",
        state_confidence="中",
        value_confidence="中",
        inference_basis="fixture",
        trigger_event_type="E_SKILL_BEGIN",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=modifiers,
    )


def _snapshot(
    *,
    hits: tuple[BattleAnalysisHit, ...],
    intervals: tuple[BattleInferredBuffInterval, ...],
    replays: tuple[BattleHitReplayResult, ...],
    max_hp_events: tuple[BattleMaxHpReductionEvent, ...] = (),
) -> BattleAnalysisSnapshot:
    effective_damage = sum(hit.damage for hit in hits) + sum(
        row.effective_hp_loss for row in max_hp_events
    )
    return BattleAnalysisSnapshot(
        battle_record_id=9,
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
        total_damage=sum(hit.damage for hit in hits),
        total_dps=sum(hit.damage for hit in hits) / 10.0,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=(),
        skills=(),
        targets=(),
        baselines=(),
        buff_intervals=intervals,
        max_hp_events=max_hp_events,
        effective_damage=effective_damage,
        effective_dps=effective_damage / 10.0,
        hit_replays=replays,
    )


class BattleBuffCounterfactualServiceTests(unittest.TestCase):
    def test_team_buff_breaks_gain_down_by_actual_damage_recipient(self) -> None:
        hits = (_hit("hit1", 115.0), _hit("hit2", 240.0, character_id=2))
        analysis = _snapshot(
            hits=hits,
            intervals=(
                _interval(
                    "buff-1",
                    target_scope="team",
                    modifiers=(_modifier(0.15),),
                ),
            ),
            replays=(_replay("hit1", 115.0), _replay("hit2", 120.0)),
        )

        with patch.object(
            BattleHitReplayService,
            "replay",
            return_value=(_replay("hit1", 100.0), _replay("hit2", 100.0)),
        ):
            (result,) = BattleBuffCounterfactualService.calculate(analysis, ())

        self.assertAlmostEqual(55.0, result.damage_gain)
        self.assertAlmostEqual(
            result.damage_gain,
            sum(row.damage_gain for row in result.beneficiaries)
            + result.unattributed_damage_gain,
        )
        self.assertEqual([1, 2], [row.character_id for row in result.beneficiaries])
        source, teammate = result.beneficiaries
        self.assertAlmostEqual(15.0, source.damage_gain)
        self.assertAlmostEqual(15.0, source.recipient_gain_percent)
        self.assertAlmostEqual(5.0, source.team_contribution_percent)
        self.assertAlmostEqual(40.0, teammate.damage_gain)
        self.assertAlmostEqual(20.0, teammate.recipient_gain_percent)
        self.assertAlmostEqual(40.0 / 300.0 * 100.0, teammate.team_contribution_percent)

    def test_removal_rate_uses_full_selected_period_without_buff_as_denominator(
        self,
    ) -> None:
        hits = (_hit("hit1", 1_150.0), _hit("hit2", 500.0, character_id=2))
        intervals = (
            _interval("buff-1", modifiers=(_modifier(0.15),)),
            _interval(
                "buff-2",
                start_us=5_000_000,
                modifiers=(_modifier(0.15),),
            ),
        )
        analysis = _snapshot(
            hits=hits,
            intervals=intervals,
            replays=(_replay("hit1", 115.0), _replay("hit2", 50.0)),
        )

        replayed_event_ids = []

        def replay_active_hits(without_analysis, *_args, **_kwargs):
            replayed_event_ids.append(tuple(row.event_id for row in without_analysis.hits))
            return (_replay("hit1", 100.0),)

        with patch.object(BattleHitReplayService, "replay", side_effect=replay_active_hits):
            (result,) = BattleBuffCounterfactualService.calculate(analysis, ())

        self.assertEqual(2, result.interval_count)
        self.assertAlmostEqual(10.0, result.coverage_seconds)
        self.assertEqual(1, result.affected_hits)
        self.assertEqual(1, result.quantified_hits)
        self.assertAlmostEqual(1_500.0, result.without_buff_damage)
        self.assertAlmostEqual(150.0, result.damage_gain)
        self.assertAlmostEqual(10.0, result.gain_percent)
        self.assertAlmostEqual(1_150.0 / 1_650.0 * 100.0, result.quantified_percent)
        self.assertEqual([("hit1", "hit2")], replayed_event_ids)

    def test_linked_nightmare_max_hp_settlement_follows_removed_buff_ratio(
        self,
    ) -> None:
        hit = _hit("hit1", 1_150.0)
        vital = BattleMaxHpReductionEvent(
            event_id="vital1",
            target_id="target",
            target_name="目标",
            observed_at_us=1_000_000,
            old_max_hp=10_000.0,
            new_max_hp=9_885.0,
            max_hp_reduction=115.0,
            hp_before_settlement=10_000.0,
            hp_ratio_before=1.0,
            effective_hp_loss=115.0,
            source_character_id=1,
            source_character_name="角色1",
            mechanic_kind="lacrimosa_nightmare_awaken_5",
            mechanic_name="噩梦生命上限结算",
            source_skill_name="噩梦",
            evidence_event_ids=("hit1",),
            attribution_confidence="中",
            calculation_confidence="中",
            inference_basis="fixture",
        )
        analysis = _snapshot(
            hits=(hit,),
            intervals=(_interval("buff-1", modifiers=(_modifier(0.15),)),),
            replays=(_replay("hit1", 115.0),),
            max_hp_events=(vital,),
        )

        with patch.object(
            BattleHitReplayService,
            "replay",
            return_value=(_replay("hit1", 100.0),),
        ):
            (result,) = BattleBuffCounterfactualService.calculate(analysis, ())

        self.assertAlmostEqual(1_100.0, result.without_buff_damage)
        self.assertAlmostEqual(165.0, result.damage_gain)
        self.assertAlmostEqual(15.0, result.gain_percent)
        self.assertAlmostEqual(1_265.0, result.quantified_damage)

    def test_unstructured_buff_remains_visible_with_zero_increment_estimate(self) -> None:
        hit = _hit("hit1", 900.0)
        analysis = _snapshot(
            hits=(hit,),
            intervals=(_interval("buff-1"),),
            replays=(_replay("hit1", 90.0),),
        )

        (result,) = BattleBuffCounterfactualService.calculate(analysis, ())

        self.assertEqual("unquantified_zero_estimate", result.method)
        self.assertEqual(1, result.affected_hits)
        self.assertEqual(0, result.quantified_hits)
        self.assertEqual(0.0, result.damage_gain)
        self.assertEqual(0.0, result.gain_percent)


if __name__ == "__main__":
    unittest.main()
