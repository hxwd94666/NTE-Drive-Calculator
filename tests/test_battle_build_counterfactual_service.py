# 验证修改配装后固定原轴的全伤害候选与分级估计。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterStat,
    BattleHitReplayResult,
    BattleInferredAction,
    BattleMaxHpReductionEvent,
    BattleRangeRoleSummary,
    BattleTimelineDamageGroup,
)
from src.services.battle_build_counterfactual_service import (
    BattleBuildCounterfactualService,
)
from src.services.battle_build_timeline_projection_service import (
    BattleBuildTimelineProjectionService,
)


def _hit(
    event_id: str,
    *,
    character_id: int,
    character_name: str,
    skill_name: str,
    damage: float,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=int(event_id.removeprefix("hit")),
        relative_time_us=int(event_id.removeprefix("hit")) * 100_000,
        character_id=character_id,
        character_name=character_name,
        skill_name=skill_name,
        damage_name=skill_name,
        damage_component="direct",
        attack_type="skill",
        damage_attribute="chaos",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=skill_name,
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
        confidence="高",
        factors=(),
        expected_damage=expected_damage,
    )


def _vital_event(
    event_id: str,
    *,
    character_id: int,
    character_name: str,
    source_skill_name: str,
    damage: float,
    evidence_event_ids: tuple[str, ...],
    mechanic_kind: str = "lacrimosa_nightmare_awaken_5",
) -> BattleMaxHpReductionEvent:
    return BattleMaxHpReductionEvent(
        event_id=event_id,
        target_id="target",
        target_name="目标",
        observed_at_us=300_000,
        old_max_hp=1_000,
        new_max_hp=1_000 - damage,
        max_hp_reduction=damage,
        hp_before_settlement=1_000,
        hp_ratio_before=1.0,
        effective_hp_loss=damage,
        source_character_id=character_id,
        source_character_name=character_name,
        mechanic_kind=mechanic_kind,
        mechanic_name="生命上限结算",
        source_skill_name=source_skill_name,
        evidence_event_ids=evidence_event_ids,
        attribution_confidence="中",
        calculation_confidence="中",
        inference_basis="fixture",
    )


def _snapshot(
    *,
    hits: tuple[BattleAnalysisHit, ...],
    baselines: tuple[BattleCharacterBaseline, ...],
    replays: tuple[BattleHitReplayResult, ...],
) -> BattleAnalysisSnapshot:
    total = sum(hit.damage for hit in hits)
    roles = tuple(
        BattleRangeRoleSummary(
            character_id=character_id,
            character_name=next(
                hit.character_name for hit in hits if hit.character_id == character_id
            ),
            hits=sum(hit.character_id == character_id for hit in hits),
            damage=sum(hit.damage for hit in hits if hit.character_id == character_id),
            dps=sum(hit.damage for hit in hits if hit.character_id == character_id) / 10,
            share_percent=(
                sum(hit.damage for hit in hits if hit.character_id == character_id)
                / total
                * 100
            ),
        )
        for character_id in dict.fromkeys(hit.character_id for hit in hits)
    )
    return BattleAnalysisSnapshot(
        battle_record_id=7,
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
        total_dps=total / 10,
        timeline_hits=hits,
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=hits,
        roles=roles,
        skills=(),
        targets=(),
        baselines=baselines,
        effective_damage=total,
        effective_dps=total / 10,
        hit_replays=replays,
    )


class BattleBuildCounterfactualServiceTests(unittest.TestCase):
    def test_every_original_hit_receives_a_candidate_from_the_estimate_ladder(self) -> None:
        hits = (
            _hit("hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100),
            _hit("hit2", character_id=1, character_name="甲", skill_name="技能甲", damage=50),
            _hit("hit3", character_id=2, character_name="乙", skill_name="技能乙", damage=80),
            _hit("hit4", character_id=3, character_name="丙", skill_name="未知机制", damage=20),
        )
        original_baselines = (
            BattleCharacterBaseline(
                1, "甲", "original", (BattleCharacterStat("AtkBase", "攻击力", 100, False),)
            ),
            BattleCharacterBaseline(
                2, "乙", "original", (BattleCharacterStat("AtkBase", "攻击力", 100, False),)
            ),
        )
        candidate_baselines = (
            original_baselines[0],
            BattleCharacterBaseline(
                2, "乙", "edited", (BattleCharacterStat("AtkBase", "攻击力", 110, False),)
            ),
        )
        original = _snapshot(
            hits=hits,
            baselines=original_baselines,
            replays=(_replay("hit1", 100),),
        )
        candidate = _snapshot(
            hits=hits,
            baselines=candidate_baselines,
            replays=(_replay("hit1", 120),),
        )

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(4, len(result.hits))
        self.assertEqual(
            (
                "structured_expected",
                "skill_peer_estimate",
                "panel_formula_estimate",
                "unchanged_estimate",
            ),
            tuple(hit.method for hit in result.hits),
        )
        self.assertAlmostEqual(288.0, result.predicted_damage)
        self.assertAlmostEqual(100.0, result.structured_damage)
        self.assertAlmostEqual(150.0, result.estimated_damage)
        self.assertAlmostEqual(40.0, result.structured_percent)
        self.assertAlmostEqual(15.2, result.gain_percent)
        self.assertEqual(3, len(result.roles))
        self.assertEqual(3, len(result.composition.roles))

    def test_comparison_rejects_different_ranges(self) -> None:
        hits = (_hit("hit1", character_id=1, character_name="甲", skill_name="A", damage=10),)
        original = _snapshot(hits=hits, baselines=(), replays=())
        candidate = _snapshot(hits=hits, baselines=(), replays=())
        candidate = replace(candidate, range_end_us=9_000_000)

        with self.assertRaisesRegex(ValueError, "同一分析时段"):
            BattleBuildCounterfactualService.compare(
                original=original,
                candidate=candidate,
            )

    def test_attributed_max_hp_damage_follows_linked_nightmare_hit(self) -> None:
        hit = _hit(
            "hit1",
            character_id=1004,
            character_name="安魂曲",
            skill_name="噩梦",
            damage=100,
        )
        original = _snapshot(
            hits=(hit,),
            baselines=(),
            replays=(_replay("hit1", 100),),
        )
        original = replace(
            original,
            effective_damage=130,
            effective_dps=13,
            max_hp_events=(_vital_event(
                "vital1",
                character_id=1004,
                character_name="安魂曲",
                source_skill_name="噩梦",
                damage=30,
                evidence_event_ids=("hit1",),
            ),),
            roles=(replace(
                original.roles[0],
                damage=130,
                dps=13,
                max_hp_reduction_damage=30,
                max_hp_reduction_events=1,
            ),),
        )
        candidate = _snapshot(
            hits=(hit,),
            baselines=(),
            replays=(_replay("hit1", 120),),
        )
        candidate = replace(candidate, max_hp_events=original.max_hp_events)

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(156.0, result.predicted_damage)
        self.assertEqual(156.0, result.roles[0].predicted_damage)
        self.assertEqual(36.0, result.roles[0].predicted_damage - 120.0)
        self.assertEqual("linked_source_hit_ratio", result.vital_events[0].method)

    def test_fadia_max_hp_damage_follows_inherent_hp(self) -> None:
        hit = _hit(
            "hit1",
            character_id=1039,
            character_name="法帝娅",
            skill_name="罪感熔炉",
            damage=100,
        )
        original = _snapshot(
            hits=(hit,),
            baselines=(BattleCharacterBaseline(
                1039,
                "法帝娅",
                "original",
                (),
                inherent_hp=10_000,
            ),),
            replays=(_replay("hit1", 100),),
        )
        event = _vital_event(
            "vital1",
            character_id=1039,
            character_name="法帝娅",
            source_skill_name="罪感熔炉",
            damage=200,
            evidence_event_ids=("hit1",),
            mechanic_kind="fadia_dark_star_max_hp_transfer",
        )
        original = replace(
            original,
            effective_damage=300,
            effective_dps=30,
            max_hp_events=(event,),
            roles=(replace(
                original.roles[0],
                damage=300,
                dps=30,
                max_hp_reduction_damage=200,
                max_hp_reduction_events=1,
            ),),
        )
        candidate = _snapshot(
            hits=(hit,),
            baselines=(BattleCharacterBaseline(
                1039,
                "法帝娅",
                "candidate",
                (),
                inherent_hp=12_000,
            ),),
            replays=(_replay("hit1", 100),),
        )
        candidate = replace(candidate, max_hp_events=(event,))

        result = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        self.assertEqual(340.0, result.predicted_damage)
        self.assertEqual(240.0, result.vital_events[0].predicted_damage)
        self.assertEqual("fadia_inherent_hp_ratio", result.vital_events[0].method)

    def test_adjusted_timeline_changes_hit_group_and_action_damage_only(self) -> None:
        hits = (
            _hit("hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100),
            _hit("hit2", character_id=1, character_name="甲", skill_name="技能甲", damage=50),
        )
        original = _snapshot(
            hits=hits,
            baselines=(),
            replays=(_replay("hit1", 100),),
        )
        candidate = _snapshot(
            hits=hits,
            baselines=(),
            replays=(_replay("hit1", 120),),
        )
        original = replace(
            original,
            effective_damage=180,
            effective_dps=18,
            max_hp_events=(_vital_event(
                "vital1",
                character_id=1,
                character_name="甲",
                source_skill_name="技能甲",
                damage=30,
                evidence_event_ids=("hit1",),
            ),),
            roles=(replace(
                original.roles[0],
                damage=180,
                dps=18,
                max_hp_reduction_damage=30,
                max_hp_reduction_events=1,
            ),),
            inferred_actions=(BattleInferredAction(
                action_id="action:1",
                character_id=1,
                character_name="甲",
                action_name="技能甲",
                input_kind="skill",
                input_sequence="E",
                start_us=0,
                end_us=500_000,
                hits=2,
                damage=150,
                identity_confidence="中",
                timing_confidence="中",
                inference_basis="fixture",
                evidence_event_ids=("hit1", "hit2"),
                gameplay_effect_ids=(),
            ),),
            timeline_damage_groups=(
                BattleTimelineDamageGroup(
                    group_id="group:skill",
                    character_id=1,
                    character_name="甲",
                    direction="outgoing",
                    channel_key="direct",
                    channel_label="直伤",
                    damage_name="技能甲",
                    source_skill_name="技能甲",
                    ability_id="技能甲",
                    start_us=100_000,
                    end_us=200_001,
                    hits=2,
                    damage=150,
                    evidence_event_ids=("hit1", "hit2"),
                ),
                BattleTimelineDamageGroup(
                    group_id="vital:vital1",
                    character_id=1,
                    character_name="甲",
                    direction="outgoing",
                    channel_key="max_hp_reduction",
                    channel_label="生命上限结算",
                    damage_name="生命上限结算",
                    source_skill_name="技能甲",
                    ability_id="fixture",
                    start_us=300_000,
                    end_us=300_001,
                    hits=1,
                    damage=30,
                    evidence_event_ids=(),
                ),
            ),
        )
        candidate = replace(candidate, max_hp_events=original.max_hp_events)
        comparison = BattleBuildCounterfactualService.compare(
            original=original,
            candidate=candidate,
        )

        projected = BattleBuildTimelineProjectionService.project(
            original,
            comparison,
        )

        self.assertEqual((120.0, 60.0), tuple(hit.damage for hit in projected.hits))
        self.assertEqual(180.0, projected.inferred_actions[0].damage)
        self.assertEqual(180.0, projected.timeline_damage_groups[0].damage)
        self.assertEqual(36.0, projected.timeline_damage_groups[1].damage)
        self.assertIn("候选/原始伤害比", projected.timeline_damage_groups[1].detail_lines[-1])
        self.assertEqual(36.0, projected.roles[0].max_hp_reduction_damage)
        self.assertEqual((100.0, 50.0), tuple(hit.damage for hit in original.hits))


if __name__ == "__main__":
    unittest.main()
