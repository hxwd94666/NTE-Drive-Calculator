# 验证配装反事实会把覆纹来源原伤害比与覆纹自身倍率比合并。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleRangeRoleSummary,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)


_CHARACTER_ID = 1036


def _hit(
    event_id: str,
    *,
    damage: float,
    classification: str,
    is_follow_up: bool,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=1_000_000,
        character_id=_CHARACTER_ID,
        character_name="残虹",
        skill_name="测试技能",
        damage_name="覆纹" if is_follow_up else "测试原伤害",
        damage_component="follow_up" if is_follow_up else "direct",
        attack_type="skill",
        damage_attribute="incantation",
        target_id="target:1",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=is_follow_up,
        classification=classification,
    )


def _source_replay(value: float) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id="source",
        observed_damage=100.0,
        non_critical_damage=value,
        critical_damage=None,
        selected_damage=value,
        selected_error_percent=0.0,
        critical_state="non_critical",
        confidence="高",
        factors=(),
        expected_damage=value,
        critical_policy="character",
    )


def _weave_replay(value: float, followup_multiplier: float) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id="weave",
        observed_damage=30.0,
        non_critical_damage=value,
        critical_damage=None,
        selected_damage=value,
        selected_error_percent=0.0,
        critical_state="not_applicable",
        confidence="高",
        factors=(
            BattleHitReplayFactor(
                factor_id="recorded_direct_damage",
                label="原伤害实际值",
                value=100.0,
                evidence_basis="同一正式事件 source",
            ),
            BattleHitReplayFactor(
                factor_id="weave_followup",
                label="覆纹追加倍率",
                value=followup_multiplier,
                evidence_basis="fixture",
            ),
        ),
        formula_type="覆纹",
        expected_damage=value,
        critical_policy="disabled",
    )


def _baseline(attack: float = 100.0) -> BattleCharacterBaseline:
    return BattleCharacterBaseline(
        character_id=_CHARACTER_ID,
        character_name="残虹",
        source="fixture",
        stats=(
            BattleCharacterStat("AtkBase", "基础攻击力", attack, False),
        ),
    )


def _snapshot(
    *,
    source_replay: BattleHitReplayResult | None,
    weave_replay: BattleHitReplayResult,
    baseline: BattleCharacterBaseline | None = None,
) -> BattleAnalysisSnapshot:
    source = _hit(
        "source",
        damage=100.0,
        classification="direct",
        is_follow_up=False,
    )
    weave = _hit(
        "weave",
        damage=30.0,
        classification="weave",
        is_follow_up=True,
    )
    hits = (source, weave)
    total = sum(hit.damage for hit in hits)
    replays = (
        (source_replay,) if source_replay is not None else ()
    ) + (weave_replay,)
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
        total_damage=total,
        total_dps=total / 10.0,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=(BattleRangeRoleSummary(
            character_id=_CHARACTER_ID,
            character_name="残虹",
            hits=2,
            damage=total,
            dps=total / 10.0,
            share_percent=100.0,
        ),),
        skills=(),
        targets=(),
        baselines=((baseline or _baseline()),),
        effective_damage=total,
        effective_dps=total / 10.0,
        hit_replays=replays,
    )


def _result_hit(result, event_id: str):
    return next(row for row in result.hits if row.event_id == event_id)


class BattleBuildWeaveSourceRatioTests(unittest.TestCase):
    def test_weave_multiplies_source_and_own_candidate_ratios(self) -> None:
        original = _snapshot(
            source_replay=_source_replay(100.0),
            weave_replay=_weave_replay(30.0, 0.30),
        )
        candidate = _snapshot(
            source_replay=_source_replay(200.0),
            weave_replay=_weave_replay(36.0, 0.36),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )
        weave = _result_hit(result, "weave")

        self.assertEqual("complete", weave.quantification.status)
        self.assertAlmostEqual(2.4, weave.quantification.quantified_ratio)
        self.assertAlmostEqual(72.0, weave.candidate_damage)

    def test_weave_does_not_repeat_an_unchanged_source_ratio(self) -> None:
        original = _snapshot(
            source_replay=_source_replay(100.0),
            weave_replay=_weave_replay(30.0, 0.30),
        )
        candidate = _snapshot(
            source_replay=_source_replay(100.0),
            weave_replay=_weave_replay(36.0, 0.36),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )
        weave = _result_hit(result, "weave")

        self.assertEqual("complete", weave.quantification.status)
        self.assertAlmostEqual(1.2, weave.quantification.quantified_ratio)
        self.assertAlmostEqual(36.0, weave.candidate_damage)

    def test_weave_preserves_gap_when_source_ratio_is_unquantifiable(self) -> None:
        original = _snapshot(
            source_replay=None,
            weave_replay=_weave_replay(30.0, 0.30),
        )
        candidate = _snapshot(
            source_replay=None,
            weave_replay=_weave_replay(36.0, 0.36),
            baseline=replace(_baseline(), stats=(
                BattleCharacterStat("AtkBase", "基础攻击力", 200.0, False),
            )),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )
        weave = _result_hit(result, "weave")

        self.assertIn(weave.quantification.status, {"partial", "unavailable"})
        self.assertIsNone(weave.candidate_damage)
        self.assertTrue(any(
            gap.dimension_id == "weave_recorded_source_hit"
            for gap in weave.quantification.gaps
        ))


if __name__ == "__main__":
    unittest.main()
