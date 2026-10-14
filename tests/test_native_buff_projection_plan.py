# 验证整批冻结区间交给原生后端、证据回填和旧组件部署边界。
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.buffs.models import BattleBuffModifierEvidence, BattleInferredBuffInterval
from src.domain.battle_report.analysis.backend_contracts import BuffProjectionBatchTooLarge
from src.integrations.analysis_core_release import create_bundled_analysis_client
from src.integrations.nte_analysis_core import ENGINE_VERSION, NativeAnalysisCancelled, NativeAnalysisError, NteAnalysisCoreClient
from src.services.battle_report.buffs.attribute_projection import BattleBuffAttributeProjectionService
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from tests.battle_report.buffs.test_attribute_projection import _hit, _interval


class PlanOracle:
    """Test-only Python oracle behind the new transport boundary."""
    supports_projection_plan = True

    def __init__(self):
        self.calls = []
        self.limit = 100_000
        self.fail_call = None

    def project_batch(self, payload, *, checkpoint=None):
        raise AssertionError("The new backend must not receive Python-selected scopes")

    def project_plan_batch(self, payload, *, checkpoint=None):
        if checkpoint:
            checkpoint()
        self.calls.append(payload)
        if len(payload["jobs"]) > self.limit:
            raise BuffProjectionBatchTooLarge("synthetic work budget")
        if self.fail_call == len(self.calls):
            raise NativeAnalysisCancelled("synthetic cancellation")
        intervals = tuple(BattleInferredBuffInterval(**{
            **row, "evidence_action_ids": tuple(row["evidence_action_ids"]),
            "evidence_event_ids": tuple(row["evidence_event_ids"]),
            "modifiers": tuple(BattleBuffModifierEvidence(**{
                **modifier,
                **{key: tuple(modifier[key]) for key in (
                    "source_require_tags", "source_ignore_tags", "target_require_tags", "target_ignore_tags")},
            }) for modifier in row["modifiers"]),
        }) for row in payload["intervals"])
        results = []
        for job in payload["jobs"]:
            hit = BattleAnalysisHit(**payload["hits"][job["hit_index"]])
            candidate = tuple(intervals[index] for index in payload["interval_sets"][job["interval_set_index"]])
            results.append(asdict(BattleBuffAttributeProjectionService.project_hit(hit, candidate)))
        return tuple(results)


class NativeBuffProjectionPlanTests(unittest.TestCase):
    def test_complete_candidate_scopes_and_time_reach_backend_without_python_selection(self):
        first = replace(_hit(), relative_time_us=1)
        alias = replace(first, event_id="same-semantics")
        later = replace(first, event_id="later", relative_time_us=11)
        other = replace(first, event_id="other", character_id=1004)
        interval = replace(_interval("scoped", start_us=0), end_us=5)
        future = replace(interval, interval_id="future", start_us=10, end_us=20)
        unknown = _interval("unresolved", start_us=0, value=None, target_scope="unknown")
        index = BattleBuffIntervalIndex((interval, future, unknown))
        excluded = index.excluding(frozenset({"scoped"}))
        backend = PlanOracle()
        memo = BattleBuffProjectionMemo(backend)
        caches = (BattleHitBuffProjectionCache(index, memo=memo),
                  BattleHitBuffProjectionCache(excluded, memo=memo))
        hits = (first, alias, later, other)
        expected = [[BattleBuffAttributeProjectionService.project_hit(hit, query) for hit in hits]
                    for query in (index, excluded)]
        with patch.object(BattleHitBuffProjectionCache, "_input", side_effect=AssertionError("Python scope scan")):
            BattleHitBuffProjectionCache.prepare_many((cache, hits) for cache in caches)
            for cache, wanted in zip(caches, expected, strict=True):
                self.assertEqual(wanted, [cache.project(replace(hit)) for hit in hits])
        self.assertEqual(1, len(backend.calls))
        wire = backend.calls[0]
        self.assertEqual(2, len(wire["interval_sets"]))
        self.assertEqual([3, 2], list(map(len, wire["interval_sets"])))
        self.assertEqual(6, len(wire["jobs"]))
        self.assertTrue(all(set(job) == {"job_id", "hit_index", "interval_set_index"} for job in wire["jobs"]))

    def test_duplicate_order_and_changed_interval_values_are_not_aliased(self):
        hit = _hit()
        interval = _interval("same-id", start_us=0, value=0.1)
        changed = replace(interval, modifiers=(replace(interval.modifiers[0], magnitude_value=0.2),))
        queries = (BattleBuffIntervalIndex((interval, interval)), BattleBuffIntervalIndex((changed,)))
        backend = PlanOracle()
        memo = BattleBuffProjectionMemo(backend)
        caches = tuple(BattleHitBuffProjectionCache(query, memo=memo) for query in queries)
        BattleHitBuffProjectionCache.prepare_many((cache, (hit,)) for cache in caches)
        sets = backend.calls[0]["interval_sets"]
        self.assertEqual(sets[0][0], sets[0][1])
        self.assertNotEqual(sets[0][0], sets[1][0])
        for query, cache in zip(queries, caches, strict=True):
            self.assertEqual(BattleBuffAttributeProjectionService.project_hit(hit, query), cache.project(hit))

    def test_work_limit_splits_in_order_and_cancellation_does_not_publish_prefix(self):
        hits = tuple(replace(_hit(), event_id=str(index), target_id=str(index)) for index in range(5))
        backend = PlanOracle()
        backend.limit = 2
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=BattleBuffProjectionMemo(backend))
        cache.prepare(hits)
        self.assertEqual([5, 2, 3, 1, 2], [len(call["jobs"]) for call in backend.calls])
        self.assertEqual([hit.event_id for hit in hits], [cache.project(hit).event_id for hit in hits])
        backend = PlanOracle()
        backend.limit = 2
        backend.fail_call = 3
        memo = BattleBuffProjectionMemo(backend)
        cache = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(()), memo=memo)
        with self.assertRaises(NativeAnalysisCancelled):
            cache.prepare(hits[:4])
        self.assertEqual({}, cache._prepared)
        self.assertEqual({}, memo._projections)

    def test_factory_keeps_installed_old_engine_and_enables_declared_new_engine(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "third_party/analysis-core/bin/nte-analysis-core.exe"
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"synthetic binary placeholder")
            (root / "data").mkdir()
            (root / "data/manifest.json").write_text(json.dumps({"database": {"dataset_id": "frozen"}}))
            for version, capabilities, supported in (
                ("0.2.0", ["buff_projection_v1"], False),
                (ENGINE_VERSION, ["buff_projection_plan_v1"], True),
                (ENGINE_VERSION, ["buff_projection_v1"], False),
            ):
                manifest = {"engine": "nte-analysis-core", "engine_version": version,
                            "capabilities": capabilities, "sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
                (binary.parent.parent / "component.json").write_text(json.dumps(manifest))
                with patch("src.integrations.analysis_core_release.bundled_root", return_value=root):
                    client = create_bundled_analysis_client(static_database_path=root / "data/static.sqlite3")
                self.assertEqual(version, client.engine_version)
                self.assertEqual(supported, client.supports_projection_plan)
                if not supported:
                    with patch.object(client, "_run") as run:
                        with self.assertRaises(NativeAnalysisError):
                            client.project_plan_batch({"jobs": []})
                    run.assert_not_called()

    def test_tools_discover_selected_binary_without_assuming_new_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nte-analysis-core.exe"
            path.touch()
            response = {"engine": "nte-analysis-core", "engine_version": "0.2.0",
                        "schema_version": "nte-analysis-response-v1", "request_schema_version": "nte-analysis-request-v1",
                        "capabilities": ["buff_projection_v1"]}
            with patch.object(NteAnalysisCoreClient, "_run", return_value=json.dumps(response).encode()):
                client = NteAnalysisCoreClient.from_executable(path, "fixture")
            self.assertEqual("0.2.0", client.engine_version)
            self.assertFalse(client.supports_projection_plan)


if __name__ == "__main__":
    unittest.main()
