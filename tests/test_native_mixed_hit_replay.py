# 验证直伤、特殊伤害与倾陷共轴批处理保持完整结果、顺序和取消边界。
from __future__ import annotations

from dataclasses import replace
import unittest

from tests.test_native_direct_replay import frozen_fixture
from src.services.battle_report.replay.direct_numeric import calculate_python_direct_formula
from src.services.battle_report.replay.service import BattleHitReplayService
from src.services.battle_report.replay.special_numeric import python_special_numeric
from src.services.battle_report.replay.topple import BattleToppleCharacterConfig


def _fixture():
    analysis, direct_evidence = frozen_fixture()
    first, second = (replace(hit, scope_half="") for hit in analysis.hits)
    topple = replace(
        first, event_id="topple", sequence=3, relative_time_us=3,
        gameplay_effect_id="Buff_Tenacity_damage", skill_name="倾陷伤害",
        damage_name="倾陷伤害", attack_type="倾陷伤害", classification="topple",
        damage_component="unknown", damage_attribute="true",
    )
    scorch = replace(
        first, event_id="scorch", sequence=4, relative_time_us=4,
        gameplay_effect_id="Buff_Reaction_5_new", skill_name="浊燃",
        damage_name="浊燃", attack_type="环合·浊燃", classification="reaction",
        damage_component="reaction",
    )
    evidence = replace(
        direct_evidence[0], event_id=scorch.event_id, damage_id="Buff_Reaction_5_new",
        damage_source_category="R", formula_kind="reaction", critical_policy="fixed",
        level_multiplier=2700.0, state_multiplier=1.0,
    )
    missing = replace(first, event_id="missing", sequence=5, relative_time_us=5)
    analysis.hits = first, topple, scorch, missing, second
    analysis.timeline_hits = analysis.hits
    return analysis, (*direct_evidence, evidence), {1: BattleToppleCharacterConfig(1, "chaos", 3603.0)}


class _MixedBackend:
    supports_battle_compute = True

    def __init__(self):
        self.direct_calls = []
        self.special_calls = []

    def calculate_batch(self, inputs, *, checkpoint=None):
        self.direct_calls.append(inputs)
        if checkpoint:
            checkpoint()
        return tuple(calculate_python_direct_formula(row) for row in inputs)

    def compute_batch(self, operation, inputs, *, checkpoint=None):
        self.special_calls.append((operation, inputs))
        if checkpoint:
            checkpoint()
        return tuple(python_special_numeric(operation, row) for row in inputs)


class NativeMixedHitReplayTests(unittest.TestCase):
    def test_direct_topple_scorch_and_missing_evidence_keep_complete_axis_results(self):
        analysis, evidence, configs = _fixture()
        expected = BattleHitReplayService.replay(
            analysis, evidence, topple_character_configs=configs, apply_observed_refinements=False,
        )
        backend = _MixedBackend()
        actual = BattleHitReplayService.replay(
            analysis, evidence, topple_character_configs=configs, apply_observed_refinements=False,
            direct_formula_backend=backend,
        )
        self.assertEqual(expected, actual)
        self.assertEqual(["direct:1", "topple", "scorch", "missing", "direct:2"],
                         [row.event_id for row in actual])
        self.assertEqual(1, len(backend.direct_calls))
        self.assertEqual(["special_topple_v1", "special_reaction_v1"],
                         [operation for operation, _ in backend.special_calls])
        self.assertIsNotNone(actual[1].selected_damage)
        self.assertIsNotNone(actual[2].critical_damage)
        self.assertEqual("unreplayable", actual[3].critical_state)

    def test_cancellation_after_direct_batch_never_returns_partial_special_axis(self):
        analysis, evidence, configs = _fixture()
        backend = _MixedBackend()

        def progress(_update):
            if backend.special_calls:
                raise InterruptedError("cancel mixed replay")
        with self.assertRaisesRegex(InterruptedError, "cancel mixed replay"):
            BattleHitReplayService.replay(
                analysis, evidence, topple_character_configs=configs,
                apply_observed_refinements=False, direct_formula_backend=backend,
                progress_callback=progress,
            )
        self.assertEqual(1, len(backend.direct_calls))
        self.assertEqual(1, len(backend.special_calls))
