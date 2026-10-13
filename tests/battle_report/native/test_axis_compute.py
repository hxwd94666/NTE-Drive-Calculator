# 验证动作、静态动画与逐击分组的原生整轴适配及完整差分。
from __future__ import annotations

from dataclasses import asdict, replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.battle_report.actions.fixtures import _hit
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.actions.inference import (
    BattleActionAnimationCandidate, BattleActionInferenceService,
)
from src.services.battle_report.timeline.projection import BattleTimelineProjectionService


def _fixture():
    hits = (
        _hit(1, 1_000_000, ability_id="GA_Test_Skill", attack_type="E技能",
             effect_id="GE_Test_Skill1"),
        _hit(2, 1_250_000, ability_id="GA_Test_Skill", attack_type="E技能",
             effect_id="GE_Test_Skill2"),
        _hit(3, 1_500_000),
        _hit(4, 1_650_000, effect_id="GE_Player_Test_Melee２_Damage"),
        _hit(5, 2_000_000, ability_id="GA_Test_UltraSkill", attack_type="Q技能",
             effect_id="GE_Test_UltraSkill1"),
        _hit(6, 3_000_000, ability_id="GA_Test_UltraSkill", attack_type="Q技能",
             effect_id="GE_Test_UltraSkill2"),
        replace(_hit(7, 4_000_000), character_id=2002, character_name="队友"),
        _hit(8, 4_100_000, ability_id="GA_Test_QTE_one", attack_type="环合·测试"),
        _hit(9, 4_300_000, ability_id="GA_Test_QTE_two", attack_type="环合·测试"),
        _hit(10, 4_700_000, effect_id="Buff_Reaction_4_new", classification="reaction"),
        _hit(11, 4_800_000, follow_up=True),
        replace(_hit(12, 5_000_000), direction="incoming", character_id=None),
        replace(_hit(13, 6_000_000, ability_id="GA_Zankou_UltraSkill", attack_type="Q技能",
                     effect_id="GE_Zankou_MagicUltraSkill1"), character_id=1036),
        replace(_hit(14, 10_000_000, ability_id="GA_Zankou_UltraSkill", attack_type="Q技能",
                     effect_id="GE_Zankou_ForceUltraSkill1"), character_id=1036),
        replace(_hit(15, 10_100_000), character_id=None),
    )
    candidate = BattleActionAnimationCandidate(
        ability_id="GA_Test_Skill", selector_key="public-candidate", montage_asset_path="fixture",
        effect_hit_offsets_us=(("GE_Test_Skill1", (100_000,)),
                               ("GE_Test_Skill2", (350_000,))),
        trigger_end_offsets_us=(700_000,), end_event_offsets_us=(),
        section_end_offsets_us=(), duration_us=800_000,
    )
    return hits, candidate


class _FixedBackend:
    supports_battle_compute = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    def compute_batch(self, operation, inputs, *, checkpoint=None):
        self.calls.append((operation, inputs))
        if checkpoint:
            checkpoint()
        return (self.response,)


class NativeAxisAdapterTests(unittest.TestCase):
    def test_action_adapter_sends_frozen_evidence_without_python_inference(self):
        hits, candidate = _fixture()
        expected = BattleActionInferenceService.infer(hits, animation_candidates=(candidate,))
        backend = _FixedBackend({"actions": [asdict(row) for row in expected]})
        with patch("src.services.battle_report.actions.inference._is_action_evidence",
                   side_effect=AssertionError("Python inference")):
            actual = BattleActionInferenceService.infer(
                hits, animation_candidates=(candidate,), compute_backend=backend,
            )
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(backend.calls))
        payload = backend.calls[0][1][0]
        hit_fields = {
            "event_id", "sequence", "relative_time_us", "character_id", "character_name",
            "direction", "is_follow_up", "classification", "ability_id", "gameplay_effect_id",
            "attack_type", "skill_name", "damage_name", "damage",
        }
        self.assertEqual(
            [{key: getattr(hit, key) for key in hit_fields} for hit in hits], payload["hits"],
        )
        self.assertNotIn("montage_asset_path", payload["animation_candidates"][0])

    def test_timeline_adapter_uses_native_indices_and_preserves_display_fields(self):
        hits, _candidate = _fixture()
        expected = BattleTimelineProjectionService.group_damage_hits(hits)
        index = {hit.event_id: ordinal for ordinal, hit in enumerate(hits)}
        backend = _FixedBackend({"groups": [
            {"indices": [index[event] for event in row.evidence_event_ids], "damage": row.damage,
             "start_us": row.start_us, "end_us": row.end_us, "channel_key": row.channel_key}
            for row in expected
        ]})
        with patch("src.services.battle_report.timeline.projection._group_key",
                   side_effect=AssertionError("Python grouping")):
            actual = BattleTimelineProjectionService.group_damage_hits(hits, compute_backend=backend)
        self.assertEqual(expected, actual)

    def test_cancel_propagates_before_publishing_actions(self):
        hits, _candidate = _fixture()
        backend = _FixedBackend({"actions": []})

        def cancel():
            raise InterruptedError
        with self.assertRaises(InterruptedError):
            BattleActionInferenceService.infer(hits, compute_backend=backend, checkpoint=cancel)


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "isolated native executable not configured")
class NativeAxisDifferentialTests(unittest.TestCase):
    def test_action_grouping_animation_hold_stop_and_interrupt_match_python(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-axis-test",
        )
        hits, candidate = _fixture()
        for hold in ("none", "during_hold", "after_hold"):
            variant = replace(candidate, hold_damage_mode=hold, hold_prelude_us=200_000)
            for candidates in ((), (variant,), (variant, replace(variant, trigger_end_offsets_us=(950_000,)))):
                for stops in ((), ((1_900_000, 3_100_000), (None, 9_000_000))):
                    for ordered in (hits, tuple(reversed(hits))):
                        kwargs = dict(animation_candidates=candidates, time_stop_intervals=stops)
                        with self.subTest(hold=hold, candidates=candidates, stops=stops):
                            expected = BattleActionInferenceService.infer(ordered, **kwargs)
                            actual = BattleActionInferenceService.infer(ordered, **kwargs, compute_backend=client)
                            self.assertEqual(expected, actual)
        self.assertEqual(BattleTimelineProjectionService.group_damage_hits(hits),
                         BattleTimelineProjectionService.group_damage_hits(hits, compute_backend=client))
