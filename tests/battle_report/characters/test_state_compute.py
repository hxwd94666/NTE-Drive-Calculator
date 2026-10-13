# 验证三种角色状态的原生差分及未知、证据顺序和旧组件边界。
from __future__ import annotations

NTE_TEST_TIER = "core"

from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.characters.state_compute import compute_character_state
from src.services.battle_report.characters.fadia import BattleFadiaHpStackService
from src.services.battle_report.characters.shinku import BattleShinkuRageBuffService
from src.services.battle_report.characters.zankou import BattleZankouFormBuffService
from tests.battle_report.characters.test_fadia import _build as fadia_build, _hit as fadia_hit, _observed_transfer
from tests.battle_report.characters.test_shinku_rage import _build as shinku_build, _hit as shinku_hit, _CONFIG as SHINKU
from tests.battle_report.characters.test_zankou_form import _build as zankou_build, _action, _CONFIG as ZANKOU


class CharacterStateAdapterTests(unittest.TestCase):
    def test_disabled_backend_keeps_oracle_and_does_not_serialize_hits(self):
        class Legacy:
            supports_battle_compute = False

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                raise AssertionError("legacy component called")
        with patch("src.services.battle_report.characters.shinku.state_rows",
                   side_effect=AssertionError("legacy payload serialization")):
            rows = BattleShinkuRageBuffService.infer(
                build=shinku_build(), hits=(shinku_hit(),), config=None, compute_backend=Legacy(),
            )
        self.assertIsNone(rows[0].modifiers[0].magnitude_value)

    def test_native_character_kind_and_cancellation_are_explicit(self):
        class Backend:
            supports_battle_compute = True
            calls = []

            def compute_batch(self, operation, inputs, *, checkpoint=None):
                self.calls.append((operation, inputs))
                return ({"groups": [], "value": None},)
        backend = Backend()
        self.assertEqual({"groups": [], "value": None}, compute_character_state(
            "shinku", {"hits": []}, backend=backend, checkpoint=None,
        ))
        self.assertEqual("character_state_v1", backend.calls[0][0])
        self.assertEqual("shinku", backend.calls[0][1][0]["kind"])
        with self.assertRaisesRegex(RuntimeError, "cancel"):
            compute_character_state("shinku", {"hits": []}, backend=backend,
                                    checkpoint=lambda: (_ for _ in ()).throw(RuntimeError("cancel")))
        self.assertEqual(1, len(backend.calls))


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"),
                     "explicit isolated battle compute executable not configured")
class CharacterStateExecutableTests(unittest.TestCase):
    def client(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-character-state-test",
        )
        self.assertTrue(client.supports_battle_compute)
        return client

    def test_zankou_transition_last_hit_switch_expiry_and_awakened_ranges(self):
        actions = (
            _action("fantasy", 1_000_000, 2_000_000, "GE_Player_Zankou_Skill1_Damage"),
            _action("fantasy-normal", 4_000_000, 5_000_000, "GE_Player_Zankou_MagicMelee1"),
            _action("out", 7_000_000, 8_000_000, character_id=1001),
            _action("fantasy-two", 9_000_000, 10_000_000, "GE_Player_Zankou_Skill2_Damage"),
            _action("q", 11_000_000, 13_000_000, "GE_Player_Zankou_MagicUltraSkill", input_kind="Q"),
            _action("reality", 17_000_000, 18_000_000, "GE_Player_Zankou_Skill3_Damage"),
        )
        hits = tuple(replace(shinku_hit(), event_id=f"{action.action_id}:hit",
                             relative_time_us=action.end_us - 100_000)
                     for action in actions)
        options = dict(actions=tuple(reversed(actions)), hits=hits, battle_end_us=25_000_000,
                       config=ZANKOU, time_stop_intervals=((2_000_000, 4_000_000), (3_000_000, 5_000_000)))
        client = self.client()
        for level in (0, 1):
            with self.subTest(level=level):
                build = zankou_build(awakening_level=level)
                expected = BattleZankouFormBuffService.infer(build=build, **options)
                with patch("src.services.battle_report.characters.zankou._has_marker",
                           side_effect=AssertionError("Python form matcher")), \
                     patch("src.services.battle_report.characters.zankou.project_timeline_time_us",
                           side_effect=AssertionError("Python time projection")):
                    actual = BattleZankouFormBuffService.infer(build=build, **options, compute_backend=client)
                self.assertEqual(expected, actual)

    def test_shinku_unknown_curve_resonance_and_simultaneous_hit_order(self):
        hits = (
            shinku_hit(), replace(shinku_hit(), event_id="same-time"),
            replace(shinku_hit(), event_id="other-role", character_id=1001),
            replace(shinku_hit("unknown"), event_id="unknown"),
            replace(shinku_hit(), event_id="later", relative_time_us=120),
        )
        client = self.client()
        for config in (None, SHINKU):
            for build in (shinku_build(), shinku_build("Effect1", "Effect3", "Effect5")):
                with self.subTest(config=config, build=build):
                    expected = BattleShinkuRageBuffService.infer(build=build, hits=hits, config=config)
                    self.assertEqual(expected, BattleShinkuRageBuffService.infer(
                        build=build, hits=hits, config=config, compute_backend=client,
                    ))

    def test_fadia_latest_observation_half_reset_plausibility_and_input_order(self):
        hits = tuple(replace(
            fadia_hit(index, index * 500_000, dark_star=True),
            scope_half="upper" if index < 7 else "lower",
        ) for index in range(14))
        events = (
            replace(_observed_transfer(60_000.0), evidence_event_ids=(hits[1].event_id, hits[2].event_id)),
            replace(_observed_transfer(9_000_000.0), evidence_event_ids=(hits[3].event_id,)),
            replace(_observed_transfer(70_000.0), evidence_event_ids=(hits[9].event_id,)),
        )
        client = self.client()
        for enabled in (False, True):
            for ordered in (hits, tuple(reversed(hits))):
                build = fadia_build(effect_three=enabled)
                options = dict(build=build, hits=ordered, battle_end_us=10_000_000, max_hp_events=events)
                expected = BattleFadiaHpStackService.infer(**options)
                with patch("src.services.battle_report.characters.fadia.resolve_fadia_source_max_hp",
                           side_effect=AssertionError("Python source HP")), \
                     patch("src.services.battle_report.characters.fadia._observed_source_hp_by_dark_star",
                           side_effect=AssertionError("Python settlement matching")):
                    actual = BattleFadiaHpStackService.infer(**options, compute_backend=client)
                self.assertEqual(expected, actual)
