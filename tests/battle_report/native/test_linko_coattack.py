# 验证同频合击的原生配对、选人窗口、竞争消歧与逐击归属差分。
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from tests.battle_report.characters.linko_fixtures import (
    _e_animation, _linko_e, _lte_aoe, _qte, _time_stops,
)
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.characters.linko_inference import (
    BattleLinkoCoattackInferenceService, BattleLinkoType6Evidence,
)


class _Backend:
    supports_battle_compute = True

    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.calls = []

    def compute_batch(self, operation, inputs, *, checkpoint=None):
        self.calls.append((operation, inputs))
        if checkpoint:
            checkpoint()
        return ({"inferences": [self.descriptor], "order": [[0, 0]]},)


class NativeLinkoCoattackAdapterTests(unittest.TestCase):
    def test_adapter_preserves_panel_owner_element_and_evidence_without_python_matching(self):
        qte, action = _qte()
        aoe = _lte_aoe()
        kwargs = dict(time_stop_projection=_time_stops(), character_elements={1036: "CHARACTER_ELEMENT_TYPE_INCANTATION"})
        expected = BattleLinkoCoattackInferenceService.infer((qte, aoe), (action,), **kwargs)
        backend = _Backend({
            "qte_action": 0, "hit_indices": [0], "trigger_kind": "qte_lte_pair",
            "confidence": "低", "basis_kind": "pair", "evidence_event_ids": [qte.event_id, aoe.event_id],
            "trigger_action_id": "", "raw_gap_us": 80_000, "active_gap_us": 80_000,
            "time_stop_source_kind": "none", "time_stop_confidence": "",
            "selection_pause_start_us": None, "selection_pause_end_us": None,
        })
        with patch("src.services.battle_report.characters.linko_inference._qte_actions",
                   side_effect=AssertionError("Python pairing")):
            actual = BattleLinkoCoattackInferenceService.infer(
                (qte, aoe), (action,), **kwargs, compute_backend=backend,
            )
        self.assertEqual(expected, actual)
        self.assertEqual(1, len(backend.calls))
        payload = backend.calls[0][1][0]
        self.assertEqual({
            "event_id", "sequence", "relative_time_us", "character_id", "direction",
            "is_follow_up", "ability_id", "gameplay_effect_id", "target_id",
        }, set(payload["hits"][0]))
        self.assertEqual({
            "action_id", "character_id", "input_kind", "start_us", "evidence_event_ids",
        }, set(payload["actions"][0]))
        self.assertEqual(action.evidence_event_ids, tuple(payload["actions"][0]["evidence_event_ids"]))
        self.assertEqual(1072, actual[0].panel_character_id)
        self.assertEqual(1036, actual[0].definition_owner_character_id)


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "isolated native executable not configured")
class NativeLinkoCoattackDifferentialTests(unittest.TestCase):
    def test_unknown_hit_and_action_roles_remain_unclaimed_without_panicking(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-linko-test",
        )
        qte, action = _qte()
        aoe = _lte_aoe()
        unknown = replace(qte, event_id="unknown:primary", character_id=None)
        for actions in ((action,), (replace(action, character_id=None),)):
            with self.subTest(unknown_action=actions[0].character_id is None):
                hits = (unknown, qte, aoe)
                kwargs = dict(time_stop_projection=_time_stops())
                expected = BattleLinkoCoattackInferenceService.infer(hits, actions, **kwargs)
                actual = BattleLinkoCoattackInferenceService.infer(
                    hits, actions, **kwargs, compute_backend=client,
                )
                self.assertEqual(expected, actual)

    def test_complete_e_type6_legacy_pair_ambiguity_and_stops_match_python(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-linko-test",
        )
        e_hits, e_action = _linko_e()
        qte, action = _qte()
        rival, rival_action = _qte(sequence=20, character_id=1003)
        aoe = _lte_aoe()
        type6 = BattleLinkoType6Evidence(
            "type6:public", 2_000_000, 4_000_000, target_id="target-1",
        )
        projections = (
            _time_stops(),
            _time_stops(((2_000_000, 4_000_000),), source_kind="nte_core", confidence="高",
                        type6_intervals=((2_000_000, 4_000_000),)),
            _time_stops(((4_000_000, 4_500_000),), source_kind="nte_core", confidence="高"),
            _time_stops(((4_000_000, 4_500_000),), source_kind="inferred_q_action", confidence="低"),
        )
        cases = (
            ((*e_hits, qte, aoe), (e_action, action)),
            ((qte, aoe), (action,)),
            ((*e_hits[:-1], qte, aoe), (e_action, action)),
            ((*e_hits, qte, rival, aoe), (e_action, action, rival_action)),
            ((*e_hits, replace(qte, target_id="other"), aoe), (e_action, action)),
        )
        for hits, actions in cases:
            for projection in projections:
                for types in ((), (type6,), (replace(type6, target_id="other"),)):
                    for fallback in (True, False):
                        kwargs = dict(time_stop_projection=projection, animation_candidates=(_e_animation(),),
                                      type6_evidence=types, allow_legacy_e_fallback=fallback,
                                      character_elements={1036: "incantation", 1003: "unknown"})
                        with self.subTest(hits=len(hits), projection=projection.source_kind, types=types, fallback=fallback):
                            expected = BattleLinkoCoattackInferenceService.infer(hits, actions, **kwargs)
                            actual = BattleLinkoCoattackInferenceService.infer(hits, actions, **kwargs, compute_backend=client)
                            self.assertEqual(expected, actual)
