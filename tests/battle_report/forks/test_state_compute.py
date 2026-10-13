# 覆盖弧盘批量状态与原 Python 完整区间差分，不自动构建原生程序。
from __future__ import annotations

NTE_TEST_TIER = "core"

from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from src.domain.battle_report import BattleBuffModifierEvidence, BattleTreatmentEvent
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.buffs.inference import BattleBuffInferenceService
from src.services.battle_report.buffs.interval_support import BattleBuffIntervalSupportMixin
from src.services.battle_report.buffs.state_compute import finalize_buff_intervals
from src.services.battle_report.forks.state_compute import compute_fork_intervals
from src.services.battle_report.forks.trigger_refinement import ForkCriticalEvent
from tests.battle_report.buffs.test_state_compute import _rule
from tests.battle_report.forks.test_refinement import _action, _hit


class ForkStateAdapterTests(unittest.TestCase):
    def test_descriptor_restores_original_rule_without_computing_state(self):
        rule = _rule("FORK_DOOR_TREATMENT_SELF")
        class Backend:
            supports_battle_compute = True
            calls = []

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                self.calls.append((operation, inputs))
                return ({"intervals": [{
                    "rule_index": 0, "kind": "fork", "suffix": "door:0",
                    "start_us": 1, "end_us": 11, "stacks": 1,
                    "basis_key": "door", "action_ids": [], "event_ids": ["treatment"],
                    "target_scope": None,
                }]},)
        backend = Backend()
        rows = compute_fork_intervals(
            (rule,), actions=(), hits=(), battle_end_us=20, time_stop_intervals=(),
            treatment_events=(), critical_events=(), backend=backend, checkpoint=None,
        )
        self.assertEqual(1, len(rows))
        self.assertEqual("buff:fork:door:0:rule", rows[0].interval_id)
        self.assertEqual(("treatment",), rows[0].evidence_event_ids)
        self.assertEqual(rule.modifiers, rows[0].modifiers)
        self.assertEqual("fork_state_v1", backend.calls[0][0])
        self.assertNotIn("intervals", backend.calls[0][1][0])

    def test_finalize_rejects_duplicate_indexes_and_does_not_publish_prefix(self):
        from tests.battle_report.buffs.test_attribute_projection import _interval
        intervals = (_interval("first", start_us=0), _interval("second", start_us=0))
        class Backend:
            supports_battle_compute = True

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                return ({"intervals": [{
                    "index": 0, "end_us": 2, "target_require_tags": [[]],
                    "boss_requirement_consumed": False,
                }] * 2},)
        with self.assertRaises(ValueError):
            finalize_buff_intervals(intervals, confirmed_all_boss=False,
                                    backend=Backend(), checkpoint=None)


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
                     "explicit isolated battle compute executable not configured")
class ForkStateExecutableTests(unittest.TestCase):
    def test_all_fork_state_families_preserve_complete_interval_evidence(self):
        events = (
            "DAMAGE_STACK_AFTER_HIT|incantation|0.3",
            "FORK_WUSHOUTIEYU_Q_BEGIN", "FORK_WUSHOUTIEYU_E_END_STACK",
            "FORK_GOLD_RECORD_QTE_ACTION_STACK",
            "FORK_DOOR_TREATMENT_SELF", "FORK_DOOR_TREATMENT_OTHERS",
            "FORK_BIT_GAME_BACKGROUND_BASE", "FORK_BIT_GAME_BACKGROUND_DAMAGE_STACK",
            "FORK_BIT_GAME_FOREGROUND_BASE", "FORK_BIT_GAME_FOREGROUND_NORMAL_PSYCHIC_STACK",
            "FORK_BITTER_CAKE_BEFORE_INCOMING_HIT", "FORK_BLAST_CANDY_Q_BEGIN",
            "FORK_BUTTERFLY_TRIGGER", "FORK_CASTLE_TRIGGER", "FORK_CROWBAR_TRIGGER",
            "FORK_KITE_TRIGGER", "FORK_GOLD_WOOL_TRIGGER", "FORK_KNIGHT_CANDY_TRIGGER",
            "FORK_LUNAR_PHASE_Q_BEGIN", "FORK_MOTOR_CANDY_FOREGROUND_PERIODIC",
            "FORK_NEST_BIRD_Q_HIT_MARK",
        )
        modifier = BattleBuffModifierEvidence(
            "AtkUp", "Additive", "fixture", 0.1, "", "高",
            target_require_tags=("Con_IsBoss", "unknown-target-tag"),
        )
        rules = tuple(_rule(
            event, rule_id=f"rule-{index}", target_asset_path=f"buff-{index}",
            source_character_id=1001, duration_seconds=2.0, cooldown_seconds=0.3,
            stack_limit_count=3, modifiers=(modifier,),
        ) for index, event in enumerate(events))
        rules += (
            _rule("ability_event|Q|source|", source_character_id=1001,
                  target_asset_path="confirmed:mofeikesi-q-team-attack",
                  duration_seconds=1.0, modifiers=(modifier,)),
            _rule("ability_event|Q|source|", source_character_id=1001,
                  target_asset_path="confirmed:mofeikesi-controlled-extra",
                  duration_seconds=5.0, modifiers=(modifier,)),
        )
        actions = tuple(replace(
            _action(index, kind, raw, raw + 350_000),
            character_id=1001 if index % 4 else 1002,
            evidence_event_ids=(f"hit:{index * 2}", f"hit:{index * 2 + 1}"),
        ) for index, (kind, raw) in enumerate(zip(
            ("Q", "E", "Q", "QTE", "A", "QTE", "E", "Q", "QTE", "E", "QTE", "Q"),
            (0, 500_000, 1_000_000, 2_000_000, 3_000_000, 4_000_000,
             4_500_000, 5_000_000, 7_500_000, 9_000_000, 12_000_000, 14_000_000),
            strict=True,
        )))
        hits = tuple(replace(
            _hit(index, raw), damage_attribute="psyche" if index % 3 else "incantation",
            direction="incoming" if index % 7 == 0 else "outgoing",
            target_id=f"target-{index % 2}",
        ) for index, raw in enumerate(range(0, 15_000_000, 200_000)))
        options = dict(
            actions=tuple(reversed(actions)), hits=tuple(reversed(hits)),
            battle_end_us=16_000_000,
            time_stop_intervals=((2_000_000, 4_000_000), (3_000_000, 5_000_000),
                                 (7_000_000, 7_500_000), (None, 10_000_000)),
            treatment_events=tuple(BattleTreatmentEvent(f"treat-{index}", time, 1001)
                                   for index, time in enumerate((0, 1_000_000, 3_000_000, 9_000_000))),
            critical_events=tuple(ForkCriticalEvent(f"crit-{index}", time, 1001)
                                  for index, time in enumerate((0, 250_000, 500_000, 2_500_000, 6_000_000, 9_000_000))),
            target_control_policy="confirmed_all_boss",
        )
        expected = BattleBuffInferenceService.infer(rules, **options)
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-fork-state-test",
        )
        self.assertTrue(client.supports_battle_compute)
        with patch.object(BattleBuffIntervalSupportMixin, "_occurrences",
                          side_effect=AssertionError("Python generic trigger")), \
             patch("src.services.battle_report.forks.refinement.infer_damage_stack_intervals",
                   side_effect=AssertionError("Python damage stack")), \
             patch("src.services.battle_report.forks.refinement.BattleForkStateRefinementService.infer_specialized",
                   side_effect=AssertionError("Python Fork state")):
            actual = BattleBuffInferenceService.infer(rules, **options, compute_backend=client)
        self.assertEqual(expected, actual)
        self.assertTrue(any(row.trigger_event_type == "FORK_MOTOR_CANDY_FOREGROUND_PERIODIC"
                            for row in actual))
        extra = tuple(row for row in actual if row.buff_asset_path.endswith("mofeikesi-controlled-extra"))
        self.assertTrue(extra)
        bases = tuple(row for row in actual if row.buff_asset_path.endswith("mofeikesi-q-team-attack"))
        self.assertTrue(all(any(base.start_us <= row.start_us < base.end_us and row.end_us <= base.end_us
                                for base in bases) for row in extra))
