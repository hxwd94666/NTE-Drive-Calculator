# 验证原生投影批处理不回退 Python，并保持完整证据、请求隔离和协议拒绝。
from __future__ import annotations

from dataclasses import asdict, replace
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.integrations.nte_analysis_core import ENGINE_VERSION, NativeAnalysisError, NteAnalysisCoreClient
from src.domain.battle_report.analysis.backend_contracts import BuffProjectionBatchTooLarge
from src.services.battle_report.buffs.attribute_projection import BattleBuffAttributeProjectionService
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from tests.battle_report.buffs.test_attribute_projection import _hit, _interval


class ProjectionBackendStub:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def project_batch(self, payload, *, checkpoint=None):
        if checkpoint:
            checkpoint()
        self.calls.append(payload)
        return tuple({**asdict(self.result), "event_id": payload["hits"][job["hit_index"]]["event_id"]}
                     for job in payload["jobs"])


class NativeBuffProjectionTests(unittest.TestCase):
    def test_prepared_owner_lookup_keeps_time_formula_owner_and_interval_query(self):
        hit = replace(_hit(), relative_time_us=1)
        other_owner = replace(hit, event_id="formula-owner", character_id=1004)
        later = replace(hit, event_id="later", relative_time_us=11)
        interval = replace(_interval("self-only", start_us=0, value=0.25), end_us=5,
                           target_scope="self", source_character_id=hit.character_id)
        hits = (hit, other_owner, later)
        expected = {item.event_id: BattleBuffAttributeProjectionService.project_hit(item, (interval,)) for item in hits}
        empty = BattleBuffAttributeProjectionService.project_hit(hit, ())

        class ScopedBackend(ProjectionBackendStub):
            def project_batch(self, payload, *, checkpoint=None):
                self.calls.append(payload)
                results = []
                for job in payload["jobs"]:
                    wire_hit = payload["hits"][job["hit_index"]]
                    temporal = payload["interval_sets"][job["temporal_set_index"]]
                    value = expected[wire_hit["event_id"]] if temporal else replace(empty, event_id=wire_hit["event_id"])
                    results.append(asdict(value))
                return tuple(results)

        backend = ScopedBackend(None)
        memo = BattleBuffProjectionMemo(backend)
        first = BattleHitBuffProjectionCache(BattleBuffIntervalIndex((interval,)), memo=memo)
        second = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=memo)
        BattleHitBuffProjectionCache.prepare_many(((first, hits), (second, (hit,))))
        calls = len(backend.calls)
        with patch.object(BattleBuffIntervalIndex, "temporal_for_hit", side_effect=AssertionError("prepared scope queried again")):
            reconstructed = tuple(replace(item) for item in hits)
            first.prepare(reconstructed)
            for item in reconstructed:
                self.assertEqual(expected[item.event_id], first.project(item))
                self.assertEqual(expected[item.event_id], first.project(replace(item)))
            second.prepare((replace(hit),))
            self.assertEqual(empty, second.project(replace(hit)))
        self.assertNotEqual(expected[hit.event_id].modifiers, expected[other_owner.event_id].modifiers)
        self.assertNotEqual(expected[hit.event_id].modifiers, expected[later.event_id].modifiers)
        self.assertEqual(calls, len(backend.calls))

    def test_only_size_limit_splits_ordered_batch_and_reanchors_every_hit(self):
        expected = BattleBuffAttributeProjectionService.project_hit(_hit(), ())
        class LimitedBackend(ProjectionBackendStub):
            def project_batch(self, payload, *, checkpoint=None):
                attempts.append(len(payload["jobs"]))
                if len(payload["jobs"]) > 2:
                    raise BuffProjectionBatchTooLarge("test size limit")
                return super().project_batch(payload, checkpoint=checkpoint)
        attempts = []
        backend = LimitedBackend(expected)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=BattleBuffProjectionMemo(backend))
        hits = tuple(replace(_hit(), event_id=str(index), target_id=str(index)) for index in range(5))
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("fallback")):
            cache.prepare(hits)
            actual = tuple(cache.project(hit) for hit in hits)
        self.assertEqual([5, 2, 3, 1, 2], attempts)
        self.assertEqual(tuple(replace(expected, event_id=str(index)) for index in range(5)), actual)
        self.assertEqual(3, len(backend.calls))

    def test_non_size_errors_are_not_retried_or_published(self):
        hits = tuple(replace(_hit(), event_id=str(index), target_id=str(index)) for index in range(3))
        for error in (NativeAnalysisError("native failure"), ValueError("invalid response")):
            with self.subTest(error=type(error).__name__):
                backend = ProjectionBackendStub(None)
                memo = BattleBuffProjectionMemo(backend)
                cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=memo)
                with patch.object(backend, "project_batch", side_effect=error) as call:
                    with self.assertRaises(type(error)):
                        cache.prepare(hits)
                self.assertEqual(1, call.call_count)
                self.assertEqual({}, cache._prepared_keys)
                self.assertEqual({}, memo._projections)

    def test_single_job_too_large_fails_and_failed_split_does_not_publish_prefix(self):
        expected = BattleBuffAttributeProjectionService.project_hit(_hit(), ())
        single = ProjectionBackendStub(expected)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=BattleBuffProjectionMemo(single))
        with patch.object(single, "project_batch", side_effect=BuffProjectionBatchTooLarge("oversize")) as call:
            with self.assertRaises(BuffProjectionBatchTooLarge):
                cache.prepare((_hit(),))
        self.assertEqual(1, call.call_count)
        attempts = []
        class FailingSplit(ProjectionBackendStub):
            def project_batch(self, payload, *, checkpoint=None):
                attempts.append(len(payload["jobs"]))
                if len(attempts) == 1:
                    raise BuffProjectionBatchTooLarge("oversize")
                if len(attempts) == 3:
                    raise NativeAnalysisError("cancelled or failed second half")
                return super().project_batch(payload, checkpoint=checkpoint)
        memo = BattleBuffProjectionMemo(FailingSplit(expected))
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=memo)
        hits = tuple(replace(_hit(), event_id=str(index), target_id=str(index)) for index in range(4))
        with self.assertRaises(NativeAnalysisError):
            cache.prepare(hits)
        self.assertEqual([4, 2, 2], attempts)
        self.assertEqual({}, cache._prepared_keys)
        self.assertEqual({}, memo._projections)

    def test_transport_byte_limit_raises_specific_size_error_before_process(self):
        hit = _hit()
        payload = {"hits": [asdict(hit)], "intervals": [], "jobs": [
            {"job_id": "0", "hit_index": 0, "active_indices": [], "temporal_indices": []}]}
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "nte-analysis-core.exe"
            executable.touch()
            client = NteAnalysisCoreClient(executable, "fixture")
            with patch("src.integrations.nte_analysis_core.MAX_BYTES", 1), patch.object(client, "_run") as run:
                with self.assertRaises(BuffProjectionBatchTooLarge):
                    client.project_batch(payload)
            run.assert_not_called()
            self.assertEqual(0, client.stats["projection_jobs"])

    def test_wire_identity_keeps_time_when_semantic_hit_signature_matches(self):
        hits = tuple(replace(_hit(), event_id=str(now), relative_time_us=now,
                             target_hp_before=25.0, target_max_hp=100.0) for now in (1, 11))
        intervals = tuple(replace(_interval(str(now), start_us=now - 1, value=0.1,
            application_requirement_asset_path="battle-fork|boxing-candy-low-hp=0.3"),
            end_us=now + 1, source_effect_definition_id="UpgradeStar_Pack_Fork_BoxingCandy") for now in (1, 11))
        result = BattleBuffAttributeProjectionService.project_hit(hits[0], intervals)
        backend = ProjectionBackendStub(result)
        memo = BattleBuffProjectionMemo(backend)
        self.assertEqual(memo.hit_token(hits[0]), memo.hit_token(hits[1]))
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(intervals), memo=memo)
        cache.prepare(hits)
        self.assertEqual(1, len(backend.calls))
        payload = backend.calls[0]
        self.assertEqual([1, 11], [payload["hits"][job["hit_index"]]["relative_time_us"] for job in payload["jobs"]])
        self.assertNotEqual(payload["jobs"][0]["temporal_set_index"], payload["jobs"][1]["temporal_set_index"])

    def test_wire_shares_ordered_scopes_across_different_hit_signatures(self):
        interval = _interval("shared", start_us=0)
        hits = tuple(replace(_hit(), event_id=str(index), target_id=str(index)) for index in range(3))
        expected = BattleBuffAttributeProjectionService.project_hit(hits[0], (interval,))
        backend = ProjectionBackendStub(expected)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex((interval,)),
                                            memo=BattleBuffProjectionMemo(backend))
        cache.prepare(hits)
        payload = backend.calls[0]
        self.assertEqual(3, len(payload["jobs"]))
        self.assertEqual([[0]], payload["interval_sets"])
        self.assertEqual({0}, {job["temporal_set_index"] for job in payload["jobs"]})
        self.assertEqual({0}, {job["active_set_index"] for job in payload["jobs"]})

    def test_prepare_deduplicates_semantics_without_running_python_rules(self):
        hit = _hit()
        interval = _interval("public", start_us=0, value=0.25)
        expected = BattleBuffAttributeProjectionService.project_hit(hit, (interval,))
        backend = ProjectionBackendStub(expected)
        memo = BattleBuffProjectionMemo(backend)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex((interval,)), memo=memo)
        alias = replace(hit, event_id="second", sequence=2, damage=2000.0)
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("fallback")):
            cache.prepare((hit, alias))
            self.assertEqual(expected, cache.project(hit))
            self.assertEqual(replace(expected, event_id="second"), cache.project(alias))
        self.assertEqual(1, len(backend.calls))
        self.assertEqual(1, len(backend.calls[0]["jobs"]))
        self.assertEqual(0, memo.rule_evaluations)

    def test_separate_requests_do_not_share_results_or_backend(self):
        hit = _hit()
        interval = _interval("public", start_us=0)
        result = BattleBuffAttributeProjectionService.project_hit(hit, (interval,))
        first, second = ProjectionBackendStub(result), ProjectionBackendStub(result)
        for backend in (first, second):
            memo = BattleBuffProjectionMemo(backend)
            BattleHitBuffProjectionCache(BattleBuffIntervalIndex((interval,)), memo=memo).prepare((hit,))
        self.assertEqual((1, 1), (len(first.calls), len(second.calls)))
        with self.assertRaises(ValueError):
            memo.bind_backend(first)

    def test_cancelled_batch_does_not_publish_partial_projection(self):
        hit = _hit()
        result = BattleBuffAttributeProjectionService.project_hit(hit, ())
        backend = ProjectionBackendStub(result)
        memo = BattleBuffProjectionMemo(backend)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=memo)
        def cancelled(_progress):
            raise RuntimeError("cancelled")
        memo.bind_backend(backend, cancelled)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            cache.prepare((hit,))
        self.assertEqual([], backend.calls)
        memo.bind_backend(backend)
        cache.prepare((hit,))
        self.assertEqual(1, len(backend.calls))

    def test_prepared_axis_larger_than_lru_never_becomes_single_hit_processes(self):
        hit = _hit()
        result = BattleBuffAttributeProjectionService.project_hit(hit, ())
        backend = ProjectionBackendStub(result)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=BattleBuffProjectionMemo(backend))
        hits = tuple(replace(hit, event_id=str(index), target_id=str(index)) for index in range(4100))
        cache.prepare(hits)
        prepared_calls = len(backend.calls)
        self.assertLess(prepared_calls, 30)
        for item in hits:
            self.assertEqual(item.event_id, cache.project(item).event_id)
            # Formula preparation may recreate the same immutable hit object.
            self.assertEqual(item.event_id, cache.project(replace(item)).event_id)
        rebuilt = tuple(replace(item) for item in hits)
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("fallback")):
            cache.prepare(rebuilt)
            for original, reconstructed in zip(hits, rebuilt, strict=True):
                expected = replace(result, event_id=original.event_id)
                self.assertEqual(expected, cache.project(original))
                self.assertEqual(expected, cache.project(reconstructed))
        self.assertEqual(prepared_calls, len(backend.calls))

    def test_transport_rejects_wrong_identity_or_incomplete_evidence(self):
        hit = _hit()
        payload = {"hits": [asdict(hit)], "intervals": [], "jobs": [
            {"job_id": "0", "hit_index": 0, "active_indices": [], "temporal_indices": []}]}
        response = {"schema_version": "nte-analysis-response-v1", "engine_version": ENGINE_VERSION,
                    "dataset_version": "fixture", "batch_kind": "buff_projection_v1",
                    "compute_elapsed_ns": 1, "result_encoding": "interned_v1",
                    "strings": [hit.event_id, "未解析"], "modifiers": [], "decisions": [],
                    "projections": [[0, [], [], [], [], 1, []]],
                    "results": [{"job_id": "0", "projection_index": 0}]}
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "nte-analysis-core.exe"
            executable.touch()
            client = NteAnalysisCoreClient(executable, "fixture")
            # JSON roundtrip also restores wire arrays from immutable domain tuples.
            good = json.loads(json.dumps(response))
            cases = []
            wrong_id = json.loads(json.dumps(good))
            wrong_id["results"][0]["job_id"] = "different"
            cases.append(wrong_id)
            missing = json.loads(json.dumps(good))
            del missing["decisions"]
            cases.append(missing)
            negative = json.loads(json.dumps(good))
            negative["results"][0]["projection_index"] = -1
            cases.append(negative)
            for malformed in cases:
                with self.subTest(response=malformed):
                    with patch.object(client, "_run", return_value=json.dumps(malformed).encode()):
                        with self.assertRaises(NativeAnalysisError):
                            client.project_batch(payload)
            self.assertEqual(0, client.stats["projection_jobs"])
            with patch.object(client, "_run", return_value=json.dumps(good).encode()):
                self.assertEqual(1, len(client.project_batch(payload)))
            self.assertEqual(1, client.stats["projection_jobs"])


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated projection executable not configured")
class NativeBuffProjectionExecutableTests(unittest.TestCase):
    def client(self):
        client = NteAnalysisCoreClient(Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-projection-test")
        self.assertEqual(ENGINE_VERSION, client.version()["engine_version"])
        return client

    def test_real_plan_matches_python_for_candidate_order_recipients_and_time_boundaries(self):
        hits = tuple(
            replace(_hit(), event_id=f"role-{role}-time-{now}", character_id=role, relative_time_us=now)
            for now in (11, 0, 5, 10, 20)
            for role in (None, 1001, 1004)
        )
        intervals = tuple(
            replace(_interval(f"scope-{scope}", start_us=0, value=0.1, target_scope=scope),
                    end_us=10, source_character_id=1001)
            for scope in ("self", "team", "team_others", "character:1004", "character:None", "target", "unknown")
        )
        later = replace(intervals[1], interval_id="later", start_us=10, end_us=20)
        full = (*intervals, intervals[1], later)
        queries = (BattleBuffIntervalIndex(full), BattleBuffIntervalIndex(tuple(reversed(full))),
                   BattleBuffIntervalIndex(full).excluding(frozenset({"scope-team"})))
        expected = tuple(tuple(BattleBuffAttributeProjectionService.project_hit(hit, query) for hit in hits)
                         for query in queries)
        client = self.client()
        self.assertTrue(client.supports_projection_plan)
        memo = BattleBuffProjectionMemo(client)
        caches = tuple(BattleHitBuffProjectionCache(query, memo=memo) for query in queries)
        with patch.object(BattleHitBuffProjectionCache, "_input", side_effect=AssertionError("Python selection")), \
             patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("Python fallback")):
            BattleHitBuffProjectionCache.prepare_many((cache, hits) for cache in caches)
            actual = tuple(tuple(cache.project(hit) for hit in hits) for cache in caches)
        self.assertEqual(expected, actual)
        self.assertEqual(1, client.stats["projection_batch_calls"])

    def test_real_compact_tables_restore_shared_evidence_and_per_hit_identity(self):
        hits = tuple(replace(_hit(), event_id=f"hit-{index}", relative_time_us=index,
                             target_hp_before=float(20 + index), target_max_hp=100.0) for index in (1, 2))
        interval = replace(_interval("shared", start_us=0, value=0.1,
            application_requirement_asset_path="battle-fork|boxing-candy-low-hp=0.3"),
            end_us=10, source_effect_definition_id="UpgradeStar_Pack_Fork_BoxingCandy")
        expected = tuple(BattleBuffAttributeProjectionService.project_hit(hit, (interval,)) for hit in hits)
        client = self.client()
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex((interval,)), memo=BattleBuffProjectionMemo(client))
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("Python fallback")):
            cache.prepare(hits)
            actual = tuple(cache.project(hit) for hit in hits)
        self.assertEqual(expected, actual)
        self.assertIs(actual[0].modifiers[0], actual[1].modifiers[0])
        self.assertIs(actual[0].decisions[0], actual[1].decisions[0])
        self.assertEqual(1, client.stats["projection_batch_calls"])
        self.assertEqual(2, client.stats["projection_jobs"])
        self.assertGreater(client.stats["projection_output_bytes"], 0)

    def test_real_time_distinct_fork_intervals_keep_both_enhanced_values(self):
        hits = tuple(replace(_hit(), event_id=f"time-{now}", relative_time_us=now,
                             target_hp_before=25.0, target_max_hp=100.0) for now in (1, 11))
        intervals = tuple(replace(_interval(str(now), start_us=now - 1, value=0.1,
            application_requirement_asset_path="battle-fork|boxing-candy-low-hp=0.3"),
            end_us=now + 1, source_effect_definition_id="UpgradeStar_Pack_Fork_BoxingCandy") for now in (1, 11))
        expected = tuple(BattleBuffAttributeProjectionService.project_hit(hit, intervals) for hit in hits)
        client = self.client()
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(intervals), memo=BattleBuffProjectionMemo(client))
        with patch.object(BattleBuffAttributeProjectionService, "project_hit", side_effect=AssertionError("Python fallback")):
            cache.prepare(hits)
            actual = tuple(cache.project(hit) for hit in hits)
        self.assertEqual(expected, actual)
        self.assertEqual((0.3, 0.3), tuple(row.modifiers[0].additive_value for row in actual))
        self.assertEqual(1, client.stats["projection_batch_calls"])
