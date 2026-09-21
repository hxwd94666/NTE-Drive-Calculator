# 验证原生输入身份门控、有界压缩结果复用和每次独立解码。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import asdict, replace
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.integrations.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.services.battle_marginal_candidate_service import BattleMarginalCandidate
from src.services.battle_native_page_cache import BattleNativePageCache, page_cache_key
from src.services.battle_native_page_service import BattleNativePageService
from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadRequest
from src.services.battle_report_persistence_service import BattleReportPersistenceDependencies
from tests.test_battle_marginal_benefit_service import _snapshot


def _raw():
    return {'analysis': json.loads(json.dumps(asdict(_snapshot()))),
            'target_catalog': {'fixture': ['original']}, 'target_catalog_error': None,
            'marginal_benefits': None, 'marginal_panel': None, 'candidate_display_analysis': None}


class PageCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.static, self.semantics, self.exe = (root / name for name in
                                               ('static.sqlite3', 'semantics.json', 'nte-analysis-core.exe'))
        for path in (self.static, self.semantics, self.exe):
            path.write_bytes(b'fixture')
        self.dependencies = BattleReportPersistenceDependencies('fixture', root / 'user.sqlite3', 8, self.static)
        self.client = Mock(supports_battle_page=True, supports_battle_page_identity=True,
                           executable=self.exe, engine_version='0.3.0', dataset_version='fixture')
        self.client.load_battle_page_identity.return_value = 'a' * 64
        self.client.load_battle_page.side_effect = lambda *args, **kwargs: _raw()
        self.current = True
        self.service = self.make_service()
        self.request = BattleReportAnalysisLoadRequest(7, detail_level='marginal', selected_character_id=1001)

    def make_service(self, dependencies=None):
        return BattleNativePageService(client=self.client, dependencies=dependencies or self.dependencies,
                                       semantics_path=self.semantics, context_is_current=lambda _: self.current)

    def test_identical_inputs_probe_but_skip_compute_and_decode_fresh(self):
        first = self.service.load(self.request)
        first.target_catalog['fixture'].append('mutated')
        events = []
        second = self.service.load(self.request, progress_callback=events.append)
        third = self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 1)
        self.assertEqual(self.client.load_battle_page_identity.call_count, 4)
        self.assertIsNot(first.analysis, second.analysis)
        self.assertIsNot(second.analysis, third.analysis)
        self.assertGreaterEqual(second.analysis.effective_damage, 0)
        self.assertEqual(second.target_catalog['fixture'], ['original'])
        self.assertEqual([event.phase for event in events], ['native_page', 'cache', 'decode', 'complete'])
        self.assertEqual(events[1].message, '正在读取已完成结果…')
        self.assertEqual(events[-1].overall_percent, 100)

    def test_a_b_a_roles_reuse_two_entries_but_other_record_clears(self):
        self.service.load(self.request)
        self.service.load(replace(self.request, selected_character_id=1002))
        self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 2)
        self.service.load(replace(self.request, battle_record_id=8))
        self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 4)

    def test_nonmarginal_role_selection_is_only_presentation(self):
        request = replace(self.request, detail_level='hit')
        self.service.load(request)
        self.service.load(replace(request, selected_character_id=1002))
        self.assertEqual(self.client.load_battle_page.call_count, 1)
        self.assertIsNone(self.client.load_battle_page.call_args.args[0]['selected_character_id'])

    def test_topple_upgrades_its_half_without_evicting_the_other_half(self):
        self.client.supports_topple_composition = True
        def compute(payload, **_kwargs):
            raw = _raw()
            raw['target_catalog']['computed_level'] = payload['detail_level']
            return raw
        self.client.load_battle_page.side_effect = compute
        upper = replace(self.request, detail_level='overview', detail_scope='first')
        lower = replace(upper, detail_scope='second')
        topple = replace(lower, detail_level='composition')
        self.service.load(upper)
        self.service.load(lower)
        self.service.load(topple)
        for _ in range(2):
            self.service.load(upper)
            result = self.service.load(lower)
            self.assertEqual('composition', result.target_catalog['computed_level'])
            self.service.load(topple)
        self.assertEqual(self.client.load_battle_page.call_count, 3)
        self.assertEqual(self.service._page_cache.entry_count, 2)
        self.client.load_battle_page_identity.return_value = 'b' * 64
        self.service.load(lower)
        self.assertEqual(self.client.load_battle_page.call_count, 4)

    def test_overview_cannot_satisfy_topple_and_topple_cannot_satisfy_hit(self):
        self.client.supports_topple_composition = True
        request = replace(self.request, detail_level='overview', detail_scope='second')
        for level in ('overview', 'composition', 'hit'):
            self.service.load(replace(request, detail_level=level))
        self.assertEqual(self.client.load_battle_page.call_count, 3)

    def test_failed_topple_keeps_both_overview_cache_entries(self):
        self.client.supports_topple_composition = True
        upper = replace(self.request, detail_level='overview', detail_scope='first')
        lower = replace(upper, detail_scope='second')
        self.service.load(upper)
        self.service.load(lower)
        self.client.load_battle_page.side_effect = NativeAnalysisError('topple failed')
        with self.assertRaises(NativeAnalysisError):
            self.service.load(replace(lower, detail_level='composition'))
        self.service.load(upper)
        self.service.load(lower)
        self.assertEqual(self.client.load_battle_page.call_count, 3)

    def test_payload_candidate_range_scope_units_and_map_order_invalidate(self):
        profile = {'character_id': 1001, 'skill_levels': {'melee': 1, 'skill': 2}}
        candidate = BattleMarginalCandidate(7, (profile,), True)
        variants = (
            replace(self.request, start_us=1, end_us=2),
            replace(self.request, detail_scope='first'),
            replace(self.request, marginal_drive_units=(('CritBase', .1),)),
            replace(self.request, marginal_candidate=candidate),
            replace(self.request, marginal_candidate=replace(candidate, disabled_inferred_fact_ids=frozenset({'public'}))),
            replace(self.request, marginal_candidate=replace(candidate, profiles=(
                {'character_id': 1001, 'skill_levels': {'skill': 2, 'melee': 1}},))),
        )
        for request in (self.request, *variants):
            self.service.load(request)
        self.assertEqual(self.client.load_battle_page.call_count, 7)

    def test_comparison_baseline_is_not_native_input_or_cache_key(self):
        self.service.load(self.request)
        self.service.load(replace(self.request, comparison_baseline=_snapshot()))
        self.assertEqual(self.client.load_battle_page.call_count, 1)
        self.assertNotIn('comparison_baseline', self.client.load_battle_page.call_args.args[0])

    def test_condition_digest_static_semantics_exe_engine_dataset_changes_invalidate(self):
        self.service.load(self.request)
        self.client.load_battle_page_identity.return_value = 'b' * 64
        self.service.load(self.request)
        for path in (self.static, self.semantics, self.exe):
            path.write_bytes(b'changed-resource')
            self.service.load(self.request)
        self.client.engine_version = 'future-engine'
        self.service.load(self.request)
        self.client.dataset_version = 'future-dataset'
        self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 7)

    def test_account_and_generation_are_isolated_and_obsolete_context_cancels_hit(self):
        self.service.load(self.request)
        for dependencies in (replace(self.dependencies, account_id='other'), replace(self.dependencies, generation=9)):
            self.make_service(dependencies).load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 3)
        self.current = False
        with self.assertRaises(CancelledError):
            self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 3)

    def test_midflight_database_or_file_change_cancels_without_caching(self):
        self.client.load_battle_page_identity.side_effect = ['a' * 64, 'b' * 64]
        with self.assertRaises(CancelledError):
            self.service.load(self.request)
        self.assertEqual(self.service._page_cache.entry_count, 0)
        self.client.load_battle_page_identity.side_effect = None

        def compute(*_args, **_kwargs):
            self.static.write_bytes(b'new-resource')
            return _raw()

        self.client.load_battle_page.side_effect = compute
        with self.assertRaises(CancelledError):
            self.service.load(self.request)
        self.assertEqual(self.service._page_cache.entry_count, 0)

    def test_failures_not_cached_but_cancelled_final_delivery_can_reuse_completed_work(self):
        self.client.load_battle_page.side_effect = NativeAnalysisError('fixture failure')
        with self.assertRaises(NativeAnalysisError):
            self.service.load(self.request)
        self.client.load_battle_page.side_effect = lambda *args, **kwargs: {}
        with self.assertRaises(NativeAnalysisError):
            self.service.load(self.request)
        self.assertEqual(self.service._page_cache.entry_count, 0)
        self.client.load_battle_page.side_effect = lambda *args, **kwargs: _raw()

        def cancel(event):
            if event.phase == 'complete':
                raise CancelledError

        with self.assertRaises(CancelledError):
            self.service.load(self.request, progress_callback=cancel)
        self.assertEqual(self.service._page_cache.entry_count, 1)
        self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 3)

    def test_compression_finishes_before_complete_and_then_checks_context(self):
        events = []
        original_put = self.service._page_cache.put

        def put(*args):
            self.assertNotIn('complete', events)
            original_put(*args)
            self.current = False

        with patch.object(self.service._page_cache, 'put', side_effect=put):
            with self.assertRaises(CancelledError):
                self.service.load(self.request, progress_callback=lambda event: events.append(event.phase))
        self.assertNotIn('complete', events)
        self.assertEqual(self.service._page_cache.entry_count, 1)

    def test_snapshot_failure_not_cached_and_successful_hit_does_not_write_again(self):
        with patch('src.services.battle_native_page_service.decode_derived_snapshot', return_value=object()):
            with patch('src.services.battle_native_snapshot_persistence.persist_native_snapshot',
                       side_effect=NativeAnalysisError('fixture persistence failure')):
                with self.assertRaises(NativeAnalysisError):
                    self.service.load(self.request)
            self.assertEqual(self.service._page_cache.entry_count, 0)
            with patch('src.services.battle_native_snapshot_persistence.persist_native_snapshot') as persist:
                self.service.load(self.request)
                self.service.load(self.request)
                persist.assert_called_once()

    def test_cache_hit_identity_probe_and_progress_still_cancel(self):
        self.service.load(self.request)

        def cancel(event):
            if event.phase == 'cache':
                raise CancelledError

        with self.assertRaises(CancelledError):
            self.service.load(self.request, progress_callback=cancel)
        self.assertEqual(self.client.load_battle_page.call_count, 1)

        def identity(*_args, **_kwargs):
            self.current = False
            return 'a' * 64

        self.client.load_battle_page_identity.side_effect = identity
        with self.assertRaises(CancelledError):
            self.service.load(self.request)
        self.assertEqual(self.client.load_battle_page.call_count, 1)

    def test_legacy_or_truthy_mock_capability_never_guesses_reuse(self):
        for capability in (False, Mock(), 1):
            self.client.supports_battle_page_identity = capability
            service = self.make_service()
            service.load(self.request)
            service.load(self.request)
        self.client.load_battle_page_identity.assert_not_called()
        self.assertEqual(self.client.load_battle_page.call_count, 6)

    def test_compressed_lru_count_and_total_bytes_are_bounded(self):
        cache = BattleNativePageCache(max_bytes=200)
        cache.select_record(7)
        for key in (b'a', b'b', b'c'):
            cache.put(key, {'value': key.decode()})
        self.assertEqual(cache.entry_count, 2)
        self.assertIsNone(cache.get(b'a'))
        self.assertLessEqual(cache.compressed_bytes, 200)
        random_text = random.Random(0).randbytes(1000).hex()
        cache.put(b'large', {'value': random_text})
        self.assertIsNone(cache.get(b'large'))
        self.assertEqual(cache.entry_count, 2)
        with patch('src.services.battle_native_page_cache.MAX_RAW_BYTES', 1):
            cache.put(b'raw-large', {'value': 1})
        self.assertIsNone(cache.get(b'raw-large'))
        cache.select_record(8)
        self.assertEqual((cache.entry_count, cache.compressed_bytes), (0, 0))
        for index in range(2):
            cache.put(str(index).encode(), {'value': random.Random(index).randbytes(100).hex()})
        self.assertEqual(cache.entry_count, 1)
        self.assertLessEqual(cache.compressed_bytes, 200)

    def test_cache_key_preserves_mapping_order(self):
        options = dict(input_digest='a' * 64, files=(), engine_version='0.3.0', dataset_version='fixture')
        self.assertNotEqual(page_cache_key({'a': 1, 'b': 2}, **options),
                            page_cache_key({'b': 2, 'a': 1}, **options))


class NativePageIdentityClientTests(unittest.TestCase):
    def test_identity_is_narrow_and_validates_digest(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'nte-analysis-core.exe'
            path.touch()
            client = NteAnalysisCoreClient(path, 'fixture', capabilities=frozenset({
                'battle_page_v1', 'battle_page_identity_v1'}))
            request = dict(account_id='fixture', generation=1, battle_record_id=7,
                           user_database_path='user', static_database_path='static', semantics_path='semantics',
                           marginal_candidate={'large': [1]}, start_us=1)
            with patch.object(client, 'load_battle_page', return_value={'input_digest': 'a' * 64}) as load:
                self.assertEqual(client.load_battle_page_identity(request), 'a' * 64)
            sent = load.call_args.args[0]
            self.assertEqual(set(sent), {'account_id', 'generation', 'battle_record_id', 'user_database_path',
                                         'static_database_path', 'semantics_path', 'detail_level'})
            self.assertEqual(sent['detail_level'], 'identity')
            for response in ({'input_digest': 'x' * 64}, {'input_digest': 'a'},
                             {'input_digest': 'a' * 64, 'other': 1}, {'input_digest': True}):
                with patch.object(client, 'load_battle_page', return_value=response):
                    with self.assertRaisesRegex(NativeAnalysisError, '身份无效'):
                        client.load_battle_page_identity(request)
            client.supports_battle_page_identity = False
            with patch.object(client, 'load_battle_page') as load:
                with self.assertRaises(NativeAnalysisError):
                    client.load_battle_page_identity(request)
                load.assert_not_called()

    def test_version_rejects_missing_advertised_identity_capability(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'nte-analysis-core.exe'
            path.touch()
            client = NteAnalysisCoreClient(path, 'fixture', capabilities=frozenset({
                'battle_page_v1', 'battle_page_identity_v1'}))
            identity = {'engine': 'nte-analysis-core', 'engine_version': '0.3.0',
                        'capabilities': ['battle_page_v1']}
            with patch.object(client, '_run', return_value=json.dumps(identity).encode()):
                with self.assertRaisesRegex(NativeAnalysisError, '输入身份能力'):
                    client.version()
