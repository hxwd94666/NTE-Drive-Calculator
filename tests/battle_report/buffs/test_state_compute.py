# 覆盖整批 Buff 触发、刷新与时停差分，并验证原生路径不调用 Python 算法。
from __future__ import annotations

NTE_TEST_TIER = "core"

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from src.domain.battle_buff_rule import BattleStaticBuffRule
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.buffs.inference import BattleBuffInferenceService
from src.services.battle_report.buffs.interval_support import BattleBuffIntervalSupportMixin
from src.services.battle_report.buffs.state_compute import compute_rule_intervals
from tests.battle_report.buffs.test_inference import _action, _hit


def _rule(event="ability_event|E|source|", **changes):
    return replace(BattleStaticBuffRule(
        rule_id="rule", source_effect_definition_id="source", source_kind="skill_effect",
        source_character_id=1072, source_character_name="角色", source_asset_path="source",
        target_asset_path="buff", target_name="增益", target_scope="self", event_type=event,
        effect_type="ADD", duration_policy="HasDuration", duration_seconds=3.0,
        stack_count=1, modifiers=(),
    ), **changes)


class Backend:
    supports_battle_compute = True

    def __init__(self, response):
        self.response = response
        self.calls = []

    def compute_batch(self, operation, inputs, *, checkpoint=None):
        self.calls.append((operation, inputs))
        if checkpoint:
            checkpoint()
        return (self.response,)


def _wire(rules, result):
    return json.loads(json.dumps({"rule_intervals": [
        [[asdict(occurrence), end] for occurrence, end in result[id(rule)]]
        for rule in rules
    ]}))


class BuffStateComputeTests(unittest.TestCase):
    def test_native_batches_all_rules_and_preserves_complete_inference(self):
        rules = (_rule(), _rule("STATIC_EQUIPPED_SOURCE", rule_id="static"))
        options = dict(actions=(_action(),), hits=(_hit(1_500_000),),
                       battle_end_us=12_000_000, time_stop_intervals=((2_000_000, 4_000_000),))
        expected = BattleBuffInferenceService.infer(rules, **options)
        backend = Backend(_wire(rules, compute_rule_intervals(rules, **options)))
        with patch.object(BattleBuffIntervalSupportMixin, "_occurrences",
                          side_effect=AssertionError("Python trigger matching")), \
             patch.object(BattleBuffIntervalSupportMixin, "_occurrence_ends",
                          side_effect=AssertionError("Python duration calculation")), \
             patch("src.services.battle_report.forks.refinement.compute_fork_intervals", return_value=None), \
             patch("src.services.battle_report.buffs.inference.finalize_buff_intervals", return_value=None):
            actual = BattleBuffInferenceService.infer(rules, **options, compute_backend=backend)
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(backend.calls))
        operation, inputs = backend.calls[0]
        self.assertEqual("buff_rule_intervals_v1", operation)
        self.assertEqual(1, len(inputs))
        self.assertEqual(2, len(inputs[0]["rules"]))
        self.assertNotIn("occurrences", inputs[0])

    def test_refresh_uses_active_cooldown_and_removal_and_preserves_evidence(self):
        rules = (
            _rule(stacking_type="RefreshWholeStack", cooldown_seconds=1.0),
            _rule("ability_event|Q|source|", rule_id="remove", effect_type="REMOVE"),
        )
        actions = tuple(replace(_action(), action_id=str(index), start_us=start,
                                end_us=start + 1, evidence_event_ids=(f"event-{index}",))
                        for index, start in enumerate((1_000_000, 2_500_000, 4_000_000, 4_500_000)))
        actions += (replace(_action(), action_id="remove", start_us=5_000_000,
                            input_kind="Q"),)
        actual = compute_rule_intervals(
            rules, actions=actions, hits=(), battle_end_us=10_000_000,
            time_stop_intervals=((2_000_000, 4_000_000),),
        )[id(rules[0])]
        self.assertEqual(1, len(actual))
        occurrence, end = actual[0]
        self.assertEqual(5_000_000, end)
        self.assertEqual(("0", "1"), occurrence.action_ids)
        self.assertEqual(("event-0", "event-1"), occurrence.event_ids)

    def test_malformed_native_rows_and_cancellation_propagate(self):
        rules = (_rule(),)
        options = dict(actions=(), hits=(), battle_end_us=10, time_stop_intervals=())
        for response in ({}, {"rule_intervals": []}, {"rule_intervals": [[[
            {"time_us": 4, "state_confidence": "低", "action_ids": [],
             "event_ids": [], "target_id": ""}, 4,
        ]]]}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                compute_rule_intervals(rules, **options, backend=Backend(response))
        backend = Backend({"rule_intervals": [[]]})
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            compute_rule_intervals(rules, **options, backend=backend,
                                   checkpoint=lambda: (_ for _ in ()).throw(RuntimeError("cancel")))
        self.assertEqual([], backend.calls)


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
                     "explicit isolated battle compute executable not configured")
class BuffStateExecutableTests(unittest.TestCase):
    def test_complete_event_families_match_python_with_order_scope_and_time_stops(self):
        events = (
            "STATIC_EQUIPPED_SOURCE", "PASSIVE_STATIC", "fork_mofeikesi_controlled_hit",
            "suit_team_attribute_hit|nature", "suit_source_attack_hit|a",
            "suit_source_reaction_after|reaction_scorch:0.5,reaction_hexed:2",
            "suit_team_reaction_after|reaction_scorch:1",
            "suit_target_state_backfill|reaction_scorch:0.5",
            "suit_target_state_forward|reaction_scorch:2",
            "passive_hit|effect,STRASSE", "passive_any_hit|effect",
            "ability_event|E|source|", "ability_event_end|Q|source|",
            "ability_event_after_end|QTE|source|",
            "ability_event_offset|E|0.0000005|", "ability_event|PERFECT_EVADE|source|",
            "buff_enter_battle", "event_begin", "buff_leave_battle", "event_finish",
            "buff_q_skill_begin", "buff_e_skill_begin", "buff_qte_begin",
            "skill_realfinish", "skill_begin", "skill_hit_before_calc",
            "skill_after_damage", "skill_after_hit", "perfect_evade",
            "parry_attack", "change_role_int", "change_role_out", "unknown",
        )
        rules = tuple(_rule(event, rule_id=f"{event}-{index}", source_character_id=1003,
                            stacking_type=stacking, stack_limit_count=limit,
                            cooldown_seconds=cooldown, duration_policy=policy,
                            duration_seconds=duration)
                      for index, event in enumerate(events)
                      for stacking, limit, cooldown, policy, duration in (
                          ("", 1, None, "HasDuration", 3.0),
                          ("RefreshWholeStack", 1, 0.5, "HasDuration", 2.0),
                          ("RefreshWholeStack", 3, None, "Infinite", None),
                          ("", 1, None, "Instant", None),
                      ))
        rules += tuple(_rule("skill_after_hit", source_character_id=1003,
                             application_requirement_asset_path=requirement)
                       for requirement in (
                           "con_mitsuki_lv6", "con_shinku_curisultradamage",
                           "con_fork_mofeikesi_1", "con_perfectevadedamage",
                           "con_selfisnotusemelee", "con_isserverorstandalone", "unknown",
                       ))
        rules += (_rule("ability_event|QTE|source|", source_character_id=1003,
                        effect_type="REMOVE"),)
        actions = tuple(replace(_action(), action_id=f"action-{index}",
                                start_us=index * 600_000, end_us=index * 600_000 + 1_000_000,
                                character_id=1003 if index % 3 else 1072, input_kind=kind,
                                action_name="闪避格挡", gameplay_effect_ids=("sagiri_ultraskill_evade_parry",))
                        for index, kind in enumerate(("E", "Q", "QTE", "A", "PERFECT_EVADE") * 2))
        hits = tuple(replace(_hit(index * 200_000), event_id=f"hit-{index}", character_id=role,
                             ability_id="GA_Sagiri_UltraSkill_Melee",
                             gameplay_effect_id="GE_Player_Sagiri_UltraSkill1_Damage_Effect_Straße",
                             damage_name="环合·浊燃" if index % 2 else "覆纹",
                             scope_half="A" if index % 3 else "a", target_id=f"target-{index % 2}",
                             is_follow_up=index % 5 == 0, direction="incoming" if index % 7 == 0 else "outgoing")
                     for index, role in enumerate((1003, None, 1072, 1003) * 9))
        options = dict(actions=tuple(reversed(actions)), hits=tuple(reversed(hits)),
                       battle_end_us=8_000_000,
                       time_stop_intervals=((2_000_000, 3_000_000), (2_500_000, 4_000_000),
                                            (None, 7_000_000), (8_500_000, 9_000_000)))
        expected = compute_rule_intervals(rules, **options)
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-buff-state-test",
        )
        self.assertTrue(client.supports_battle_compute)
        with patch.object(BattleBuffIntervalSupportMixin, "_occurrences",
                          side_effect=AssertionError("Python trigger matching")):
            self.assertEqual(expected, compute_rule_intervals(rules, **options, backend=client))
