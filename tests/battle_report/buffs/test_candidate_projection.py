# 验证联合候选批次保留正式计划、独立区间视图、公式供体和取消边界。
from __future__ import annotations

NTE_TEST_TIER = "core"

from dataclasses import replace
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from src.services.battle_report.buffs.attribute_projection import BattleBuffAttributeProjectionService
from src.services.battle_report.buffs.candidate_projection import prepare_buff_candidate_projection_batch
from src.services.battle_report.buffs.counterfactual_plan import BattleBuffCounterfactualPlanService
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.formula_projection import project_formula_hit
from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from tests.battle_report.buffs.test_attribute_projection import _hit, _interval
from tests.battle_report.replay.test_selected_context import _evidence
from tests.test_native_buff_projection import ProjectionBackendStub


def _inputs():
    hits = (replace(_hit(), event_id="early", relative_time_us=1),
            replace(_hit(), event_id="later", relative_time_us=11, target_id="other"))
    intervals = (replace(_interval("early-buff", start_us=0, value=0.1), end_us=5,
                         source_effect_definition_id="first"),
                 replace(_interval("later-buff", start_us=10, value=0.2), end_us=15,
                         source_effect_definition_id="second"))
    index = BattleBuffIntervalIndex(intervals)
    plans = BattleBuffCounterfactualPlanService.prepare(hits, intervals, index)
    return hits, intervals, index, plans


class BuffCandidateProjectionBatchTests(unittest.TestCase):
    def test_joint_request_prepares_full_formula_axis_and_preserves_views(self):
        hits, intervals, index, plans = _inputs()
        backend = ProjectionBackendStub(BattleBuffAttributeProjectionService.project_hit(hits[0], ()))
        memo = BattleBuffProjectionMemo(backend)
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("fallback")):
            pairs = prepare_buff_candidate_projection_batch(plans, outgoing_hits=hits,
                interval_index=index, memo=memo, evidence_by_event={})
            self.assertEqual(1, len(backend.calls))
            for plan in plans:
                pair = pairs[plan.group_key]
                pair.require_inputs(index, plan.intervals, plan.active_hits, memo)
                removed = {row.interval_id for row in plan.intervals}
                self.assertEqual(tuple(row for row in intervals if row.interval_id not in removed), pair.without_index.intervals)
                for hit in hits:
                    self.assertEqual(hit.event_id, pair.candidate_cache.project(replace(hit)).event_id)
                for hit in plan.active_hits:
                    pair.group_cache.project(pair.consumer_hits[hit.event_id])
            self.assertEqual(1, len(backend.calls))
        with self.assertRaises(ValueError):
            pair.require_inputs(BattleBuffIntervalIndex(intervals), plan.intervals, plan.active_hits, memo)
        with self.assertRaises(ValueError):
            pair.require_inputs(index, plan.intervals, plan.active_hits, BattleBuffProjectionMemo(backend))

    def test_group_consumer_uses_formal_linko_context_but_ordinary_owner_keeps_raw_hit(self):
        hits, _, index, plans = _inputs()
        evidence = {
            "early": replace(_evidence("early"), source_character_id=1004, panel_character_id=1004,
                             formula_context_kind="linko_coattack:fixture"),
            "later": replace(_evidence("later"), source_character_id=1004, panel_character_id=1004),
        }
        backend = ProjectionBackendStub(BattleBuffAttributeProjectionService.project_hit(hits[0], ()))
        pairs = prepare_buff_candidate_projection_batch(plans, outgoing_hits=hits,
            interval_index=index, memo=BattleBuffProjectionMemo(backend), evidence_by_event=evidence)
        consumers = {event: hit for pair in pairs.values() for event, hit in pair.consumer_hits.items()}
        self.assertEqual(1004, consumers["early"].character_id)
        self.assertEqual(1072, consumers["later"].character_id)
        self.assertEqual("linko_coattack:fixture", consumers["early"].formula_context_kind)

    def test_cancelled_joint_preparation_never_starts_backend_and_python_oracle_stays_lazy(self):
        hits, _, index, plans = _inputs()
        self.assertEqual({}, prepare_buff_candidate_projection_batch(plans, outgoing_hits=hits,
            interval_index=index, memo=BattleBuffProjectionMemo(), evidence_by_event={}))
        backend = ProjectionBackendStub(None)
        def cancel(_progress):
            raise RuntimeError("cancelled before batch")
        with self.assertRaisesRegex(RuntimeError, "cancelled before batch"):
            prepare_buff_candidate_projection_batch(plans, outgoing_hits=hits,
                interval_index=index, memo=BattleBuffProjectionMemo(backend), evidence_by_event={}, progress_callback=cancel)
        self.assertEqual([], backend.calls)

    def test_full_axis_preparation_includes_exact_paired_weave_formula_owner(self):
        source = replace(_hit(), event_id="source", character_id=1004)
        weave = replace(_hit(), event_id="weave", classification="weave", is_follow_up=True)
        hits = (source, weave)
        intervals = (_interval("team", start_us=0, target_scope="team"),)
        index = BattleBuffIntervalIndex(intervals)
        plans = BattleBuffCounterfactualPlanService.prepare(hits, intervals, index)
        backend = ProjectionBackendStub(BattleBuffAttributeProjectionService.project_hit(source, ()))
        pair = next(iter(prepare_buff_candidate_projection_batch(plans, outgoing_hits=hits,
            interval_index=index, memo=BattleBuffProjectionMemo(backend), evidence_by_event={}).values()))
        calls = len(backend.calls)
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("fallback")):
            paired = pair.candidate_cache.project(replace(weave, character_id=source.character_id))
            raw = pair.candidate_cache.project(weave)
        self.assertEqual("weave", paired.event_id)
        self.assertEqual("weave", raw.event_id)
        self.assertEqual(calls, len(backend.calls))

    @unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated projection executable not configured")
    def test_real_joint_candidate_views_match_python_for_every_full_axis_hit(self):
        hits, _, index, plans = _inputs()
        client = NteAnalysisCoreClient(Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-joint-projection")
        expected = {}
        for plan in plans:
            query = index.excluding(frozenset(row.interval_id for row in plan.intervals))
            expected[plan.group_key] = tuple(BattleBuffAttributeProjectionService.project_hit(hit, query) for hit in hits)
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("fallback")):
            pairs = prepare_buff_candidate_projection_batch(plans, outgoing_hits=hits,
                interval_index=index, memo=BattleBuffProjectionMemo(client), evidence_by_event={})
            for plan in plans:
                actual = tuple(pairs[plan.group_key].candidate_cache.project(project_formula_hit(hit, None)) for hit in hits)
                self.assertEqual(expected[plan.group_key], actual)
        self.assertEqual(1, client.stats["projection_batch_calls"])
