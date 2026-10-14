# 验证 DOT 状态与残虹双 Q 蓄焰的完整原生差分，不隐式构建程序。
from __future__ import annotations

from dataclasses import asdict, replace
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.mechanics.dot_state import reconstruct_dot_stack_states
from src.services.battle_report.characters.zankou import reconstruct_zankou_q_final_damage
from tests.battle_report.mechanics.test_dot_state import _hit
from tests.battle_report.characters.test_zankou_form import _action


class HitStateAdapterTests(unittest.TestCase):
    def test_early_settlement_evidence_preserves_unknown_coefficient_and_clears_stack(self):
        # 正式三觉终段后的结算不是普通跳伤；证据文案须与未解析系数保持一致。
        analysis = SimpleNamespace(hits=(
            _hit("apply", 0, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            _hit("ordinary", 100_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
            _hit("finish", 200_000, "GE_Player_Lacrimosa_Melee5_Damage", 1004),
            _hit("early", 300_000, "GE_Player_Lacrimosa_Blood_Damage_LV6", 1004),
            _hit("after", 400_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
        ), time_stop_intervals=())
        build = {"characters": [{"character_id": 1004,
                                 "profile": {"selected_awaken_effect_ids": ["Effect3"]}}]}
        states = reconstruct_dot_stack_states(analysis, build)
        early = states["early"]
        self.assertEqual((0, "未解析", "三觉：噩梦提前结算"),
                         (early.coefficient, early.confidence, early.label))
        self.assertIn("剩余伤害结算", early.evidence_basis)
        self.assertIn("本击不按普通噩梦跳伤估算", early.evidence_basis)
        self.assertNotIn("剩余伤害结算", states["ordinary"].evidence_basis)
        self.assertEqual((1, "低", "噩梦观测下限"),
                         (states["after"].coefficient, states["after"].confidence,
                          states["after"].label))
        self.assertIn("不反推精确层数", states["after"].evidence_basis)

    def test_native_dot_restores_the_same_weak_evidence_without_import_cycle(self):
        class Backend:
            supports_battle_compute = True
            def compute_batch(self, operation, inputs, *, checkpoint=None):
                return ({"states": [{
                    "event_id": "tick", "kind": "nightmare", "coefficient": 1,
                    "confidence": "低", "lower": True, "early": False,
                    "final_enabled": False, "final_active": False,
                    "active_dot_kind_count": 1, "dot_final_multiplier": 1.0,
                    "effect": "ge_player_lacrimosa_blood_damage",
                    "active_kinds": ["nightmare"], "recent_only": [],
                }]},)
        analysis = SimpleNamespace(
            hits=(_hit("tick", 1_000_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),),
            time_stop_intervals=(),
        )
        expected = reconstruct_dot_stack_states(analysis, None)
        actual = reconstruct_dot_stack_states(analysis, None, compute_backend=Backend())
        self.assertEqual(asdict(expected["tick"]), asdict(actual["tick"]))

    def test_native_q_preserves_branch_metadata_without_python_trigger_filter(self):
        class Backend:
            supports_battle_compute = True
            def compute_batch(self, operation, inputs, *, checkpoint=None):
                return ({"states": [{"event_id": "q", "branch": "magic", "multiplier": 2.5}]},)
        character = {"character_id": 1036, "awakening_level": 4}
        analysis = SimpleNamespace(hits=(), inferred_actions=())
        with patch("src.services.battle_report.characters.zankou._is_charge_trigger",
                   side_effect=AssertionError("Python charge trigger")):
            rows = reconstruct_zankou_q_final_damage(analysis, character, compute_backend=Backend())
        self.assertEqual(2.5, rows["q"].multiplier)
        self.assertIn("血宴分支独立持有", rows["q"].evidence_basis)


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
                     "explicit isolated hit state executable not configured")
class HitStateExecutableTests(unittest.TestCase):
    def client(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-hit-state-test",
        )
        self.assertTrue(client.supports_battle_compute)
        return client

    def test_all_dot_kinds_expiry_scorch_activation_cast_batch_and_early_settlement(self):
        entries = (
            (0, "GE_Player_Lacrimosa_Melee1_Damage", 1004),
            (100_000, "GE_Player_Lacrimosa_Melee2_Damage", 1004),
            (200_000, "GE_Player_Zankou_Skill1_1_Damage", 1036),
            (300_000, "Buff_Reaction_5_new", 1003),
            (400_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
            (500_000, "GE_Player_Zankou_DotDamage", 1036),
            (600_000, "Buff_Reaction_5_new_1036", 1036),
            (700_000, "GE_Player_Cang_UltraSkill2_Damage", 1023),
            (800_000, "GE_Player_Cang_UltraSkill_Damage", 1023),
            (1_000_000, "GE_Player_Adler_Skill2_Damage", 1033),
            (1_100_000, "GE_Player_Adler_Skill_Damage", 1033),
            (1_200_000, "GE_Player_Zankou_MagicUltraSkill1_Damage", 1036),
            (1_400_000, "GE_Player_Zankou_MagicUltraSkill2_Damage", 1036),
            (1_500_000, "GE_Player_Zankou_DotUltraDamage", 1036),
            (1_600_000, "GE_Player_Zankou_MagicMelee1_Damage", 1036),
            (1_700_000, "GE_Player_Lacrimosa_Melee5_Damage", 1004),
            (1_800_000, "GE_Player_Lacrimosa_Blood_Damage_LV6", 1004),
            (4_000_000, "GE_Player_Lacrimosa_Blood_Damage", 1004),
            (12_000_000, "GE_Player_Cang_UltraSkill_Damage", 1023),
            (17_000_000, "GE_Player_Adler_Skill_Damage", 1033),
            (50_000_000, "GE_Player_Zankou_DotDamage", 1036),
        )
        hits = tuple(_hit(str(index), time, effect, owner)
                     for index, (time, effect, owner) in enumerate(entries))
        hits += (
            replace(hits[2], event_id="scorch-labelled-direct", damage_name="环合·浊燃", relative_time_us=250_000),
            replace(hits[5], event_id="explicit-scorch", damage_name="浊燃", relative_time_us=550_000),
            replace(hits[4], event_id="other-target", target_id="other", scope_half="LOWER"),
            replace(hits[4], event_id="incoming", direction="incoming"),
        )
        analysis = SimpleNamespace(hits=tuple(reversed(hits)),
                                   time_stop_intervals=((1_900_000, 3_500_000), (3_000_000, 4_500_000)))
        client = self.client()
        for stage in (None, 0, 2):
            for enabled in (False, True):
                zankou = {"character_id": 1036}
                if stage is not None:
                    zankou["breakthrough_stage"] = stage
                build = {"characters": [
                    zankou, {"character_id": 1003, "breakthrough_stage": 2 if enabled else 0},
                    {"character_id": 1004, "profile": {"selected_awaken_effect_ids": ["Effect3", "Effect4"]}},
                    {"character_id": 1023, "profile": {"selected_awaken_effect_ids": ["Effect5"]}},
                ]}
                expected = reconstruct_dot_stack_states(analysis, build)
                with patch("src.services.battle_report.mechanics.dot_state._Stack.advance",
                           side_effect=AssertionError("Python DOT stack")), \
                     patch("src.services.battle_report.mechanics.dot_state._burst_final_markers",
                           side_effect=AssertionError("Python burst markers")):
                    actual = reconstruct_dot_stack_states(analysis, build, compute_backend=client)
                self.assertEqual(expected, actual)

    def test_two_q_branches_consume_independently_and_same_time_trigger_is_not_early(self):
        trigger = replace(_hit("charge", 0, "unknown", 1036), classification="weave")
        magic = _hit("magic", 2_000_000, "GE_Player_Zankou_MagicUltraSkill_Damage", 1036)
        force = _hit("force", 3_000_000, "GE_Player_Zankou_ForceUltraSkill_Damage", 1036)
        later = replace(force, event_id="later", relative_time_us=6_000_000)
        actions = tuple(replace(
            _action(hit.event_id, hit.relative_time_us - 500_000, hit.relative_time_us, input_kind="Q"),
            evidence_event_ids=(hit.event_id,),
        ) for hit in (magic, force, later))
        analysis = SimpleNamespace(hits=(trigger, magic, force, later), inferred_actions=tuple(reversed(actions)))
        client = self.client()
        for level in (2, 4):
            character = {"character_id": 1036, "awakening_level": level}
            expected = reconstruct_zankou_q_final_damage(analysis, character)
            with patch("src.services.battle_report.characters.zankou._is_charge_trigger",
                       side_effect=AssertionError("Python charge trigger")):
                self.assertEqual(expected, reconstruct_zankou_q_final_damage(
                    analysis, character, compute_backend=client,
                ))
