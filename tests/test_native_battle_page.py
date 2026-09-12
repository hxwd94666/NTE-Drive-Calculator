# 验证原生整页接口只发送选择器，拒绝跨账号响应并保留未知展示值。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

from src.domain.battle_report import BattleAnalysisSnapshot, BattleInferredBuffInterval
from src.integrations.native_battle_page_wire import decode
from src.integrations.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.services.battle_native_page_service import BattleNativePageService
from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadRequest
from src.services.battle_report_persistence_service import BattleReportPersistenceDependencies
from tests.test_battle_marginal_benefit_service import _snapshot


class NativeBattlePageTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.static_path = Path(directory.name) / 'static.sqlite3'
        self.semantics_path = Path(directory.name) / 'semantics.json'
        self.static_path.touch()
        self.semantics_path.write_text('{}', encoding='utf-8')

    def test_wire_preserves_unknown_optional_damage_and_rejects_boolean_number(self):
        snapshot = _snapshot()
        raw = json.loads(json.dumps(asdict(snapshot)))
        self.assertEqual(decode(raw, BattleAnalysisSnapshot), snapshot)
        raw['effective_damage'] = True
        with self.assertRaises(NativeAnalysisError):
            decode(raw, BattleAnalysisSnapshot)

    def test_missing_or_old_component_does_not_fall_back_to_python(self):
        dependencies = BattleReportPersistenceDependencies('fixture', Path('missing-user.sqlite3'), 8, self.static_path)
        for client in (None, Mock(supports_battle_page=False)):
            service = BattleNativePageService(client=client, dependencies=dependencies,
                semantics_path=self.semantics_path, context_is_current=lambda _: True)
            with self.assertRaisesRegex(NativeAnalysisError, '数据库直读'):
                service.load(BattleReportAnalysisLoadRequest(7))

    def test_production_factory_injects_database_page_loader(self):
        from types import SimpleNamespace
        from src.app.context import ApplicationPaths
        from src.features.battle_report.dependencies import BattleReportServiceFactory
        root = self.static_path.parent
        bundle = root / '_internal'
        paths = ApplicationPaths.from_roots(
            root=bundle, app_dir=root, data_root=root / 'user-data',
            bundled_config_dir=bundle / 'config', asset_dir=bundle / 'assets',
            app_icon_path=bundle / 'assets/app_icon.ico', static_database_path=self.static_path,
        )
        semantics_path = paths.bundled_config_dir / 'gameplay_effect_semantics.json'
        semantics_path.parent.mkdir(parents=True)
        semantics_path.write_text('{}', encoding='utf-8')
        dependencies = BattleReportPersistenceDependencies('fixture', root / 'user.sqlite3', 8, self.static_path)
        context = SimpleNamespace(paths=paths, generation=8, account=SimpleNamespace(
            active_account_id='fixture', user_database_path=dependencies.user_database_path,
        ))
        client = Mock(supports_battle_page=True, load_battle_page=Mock(return_value={}))
        for user_override in (False, True):
            with self.subTest(user_override=user_override):
                if user_override:
                    paths.config_dir.mkdir(parents=True)
                    (paths.config_dir / semantics_path.name).write_text('invalid override', encoding='utf-8')
                with patch('src.features.battle_report.dependencies.create_bundled_analysis_client', return_value=client):
                    with patch('src.features.battle_report.dependencies.BattleReportHistoryService') as history:
                        BattleReportServiceFactory(context).history_service(dependencies)
                loader = history.call_args.kwargs['native_page_loader']
                self.assertIsInstance(loader, BattleNativePageService)
                self.assertIs(history.call_args.kwargs['direct_formula_backend'], client)
                with patch('src.services.battle_native_page_service.decode_page', return_value='rendered'):
                    self.assertEqual(loader.load(BattleReportAnalysisLoadRequest(7)), 'rendered')
                payload = client.load_battle_page.call_args.args[0]
                self.assertEqual(Path(payload['semantics_path']), semantics_path.resolve())

    def test_missing_release_resource_reports_error_and_can_retry_after_repair(self):
        dependencies = BattleReportPersistenceDependencies('fixture', Path('user.sqlite3'), 8, self.static_path)
        client = Mock(supports_battle_page=True, load_battle_page=Mock(return_value={}))
        service = BattleNativePageService(client=client, dependencies=dependencies,
            semantics_path=self.semantics_path, context_is_current=lambda _: True)
        self.semantics_path.unlink()
        with self.assertRaisesRegex(NativeAnalysisError, '发行资源缺失') as raised:
            service.load(BattleReportAnalysisLoadRequest(7))
        self.assertNotIn(str(self.semantics_path.parent), str(raised.exception))
        self.assertNotIn(str(self.semantics_path.parent), ''.join(traceback.format_exception(raised.exception)))
        client.load_battle_page.assert_not_called()
        self.semantics_path.write_text('{}', encoding='utf-8')
        with patch('src.services.battle_native_page_service.decode_page', return_value='rendered'):
            self.assertEqual(service.load(BattleReportAnalysisLoadRequest(7)), 'rendered')

    def test_wire_rejects_unrecognized_fields(self):
        raw = json.loads(json.dumps(asdict(_snapshot())))
        raw['made_up_damage'] = 123
        with self.assertRaises(NativeAnalysisError):
            decode(raw, BattleAnalysisSnapshot)

    def test_native_buff_window_survives_full_page_analysis_decode(self):
        interval = dict(
            interval_id='fixture', buff_asset_path='fixture', buff_name='fixture',
            source_effect_definition_id='', source_kind='native', source_character_id=1004,
            source_character_name='fixture', target_scope='self', start_us=0, end_us=200,
            stacks=1, duration_policy='duration', state_confidence='medium',
            value_confidence='unknown', inference_basis='fixture', trigger_event_type='',
            evidence_action_ids=[], evidence_event_ids=[], modifiers=[], native_window_end_us=100,
        )
        raw = json.loads(json.dumps(asdict(_snapshot())))
        raw['buff_intervals'] = [interval]
        raw['timeline_buff_intervals'] = [interval]
        decoded = decode(raw, BattleAnalysisSnapshot | None)
        self.assertEqual(decoded.buff_intervals[0].native_window_end_us, 100)
        self.assertEqual(decoded.timeline_buff_intervals[0].native_window_end_us, 100)
        for invalid in (True, '100', 100.5):
            with self.subTest(invalid_type=type(invalid).__name__):
                with self.assertRaises(NativeAnalysisError):
                    decode(dict(interval, native_window_end_us=invalid), BattleInferredBuffInterval)
        interval.pop('native_window_end_us')
        self.assertIsNone(decode(interval, BattleInferredBuffInterval).native_window_end_us)
        interval['native_window_end_us'] = None
        self.assertIsNone(decode(interval, BattleInferredBuffInterval).native_window_end_us)

    def test_request_never_serializes_cached_report_or_reads_database_in_python(self):
        client = Mock(load_battle_page=Mock(return_value={}))
        dependencies = BattleReportPersistenceDependencies('fixture', Path('missing-user.sqlite3'), 8,
                                                           self.static_path)
        service = BattleNativePageService(client=client, dependencies=dependencies,
                                         semantics_path=self.semantics_path, context_is_current=lambda _: True)
        request = BattleReportAnalysisLoadRequest(7, detail_level='marginal', comparison_baseline=_snapshot())
        with patch('src.services.battle_native_page_service.decode_page', return_value='rendered'):
            self.assertEqual(service.load(request), 'rendered')
        payload = client.load_battle_page.call_args.args[0]
        self.assertNotIn('comparison_baseline', payload)
        self.assertNotIn('hits', payload)
        self.assertNotIn('build', payload)
        self.assertEqual(payload['generation'], 8)
        self.assertEqual(payload['battle_record_id'], 7)

    def test_account_change_discards_result_after_process_finishes(self):
        current = True

        def run(_request, **_kwargs):
            nonlocal current
            current = False
            return {}

        client = Mock(load_battle_page=run)
        dependencies = BattleReportPersistenceDependencies('fixture', Path('user.sqlite3'), 8, self.static_path)
        service = BattleNativePageService(client=client, dependencies=dependencies,
                                         semantics_path=self.semantics_path, context_is_current=lambda _: current)
        with patch('src.services.battle_native_page_service.decode_page', return_value='obsolete'):
            with self.assertRaises(CancelledError):
                service.load(BattleReportAnalysisLoadRequest(7))

    def test_progress_reports_actual_counts_without_checkpoint_overwriting_phase(self):
        events = []

        def run(_request, *, checkpoint, progress_callback):
            progress_callback(dict(kind='battle_progress_v1', phase='buff_remove', completed=2, total=7))
            checkpoint()
            checkpoint()
            self.assertEqual(events[-1].phase, 'buff_remove')
            return {}

        dependencies = BattleReportPersistenceDependencies('fixture', Path('user.sqlite3'), 8, self.static_path)
        service = BattleNativePageService(client=Mock(load_battle_page=run), dependencies=dependencies,
            semantics_path=self.semantics_path, context_is_current=lambda _: True)
        with patch('src.services.battle_native_page_service.decode_page', return_value='rendered'):
            service.load(BattleReportAnalysisLoadRequest(7), progress_callback=events.append)
        self.assertEqual([event.phase for event in events], ['native_page', 'buff_remove', 'decode', 'complete'])
        self.assertEqual((events[1].completed, events[1].total), (2, 7))
        self.assertEqual(events[0].overall_percent, 1)
        self.assertEqual(events[-1].overall_percent, 100)
        self.assertTrue(all(event.overall_percent < 100 for event in events[:-1]))

    def test_progress_from_invalidated_account_is_not_delivered(self):
        current = True
        events = []

        def run(_request, *, checkpoint, progress_callback):
            nonlocal current
            current = False
            progress_callback(dict(kind='battle_progress_v1', phase='buff_remove', completed=2, total=7))
            self.fail('obsolete native request must be cancelled')

        dependencies = BattleReportPersistenceDependencies('fixture', Path('user.sqlite3'), 8, self.static_path)
        service = BattleNativePageService(client=Mock(load_battle_page=run), dependencies=dependencies,
            semantics_path=self.semantics_path, context_is_current=lambda _: current)
        with self.assertRaises(CancelledError):
            service.load(BattleReportAnalysisLoadRequest(7), progress_callback=events.append)
        self.assertEqual([event.phase for event in events], ['native_page'])

    def test_decode_failure_never_reports_one_hundred_percent(self):
        events = []
        dependencies = BattleReportPersistenceDependencies('fixture', Path('user.sqlite3'), 8, self.static_path)
        service = BattleNativePageService(client=Mock(load_battle_page=Mock(return_value={})),
            dependencies=dependencies, semantics_path=self.semantics_path, context_is_current=lambda _: True)
        with patch('src.services.battle_native_page_service.decode_page', side_effect=NativeAnalysisError('invalid')):
            with self.assertRaises(NativeAnalysisError):
                service.load(BattleReportAnalysisLoadRequest(7), progress_callback=events.append)
        self.assertTrue(events)
        self.assertTrue(all(event.overall_percent < 100 for event in events))

    def test_static_replacement_discards_result(self):
        def run(_request, **_kwargs):
            self.static_path.write_bytes(b'changed static release')
            return {}
        dependencies = BattleReportPersistenceDependencies('fixture', Path('user.sqlite3'), 8, self.static_path)
        service = BattleNativePageService(client=Mock(load_battle_page=run), dependencies=dependencies,
                                         semantics_path=self.semantics_path, context_is_current=lambda _: True)
        with patch('src.services.battle_native_page_service.decode_page', return_value='obsolete'):
            with self.assertRaises(CancelledError):
                service.load(BattleReportAnalysisLoadRequest(7))

    def test_transport_rejects_different_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / 'nte-analysis-core.exe'
            executable.touch()
            client = NteAnalysisCoreClient(executable, 'fixture', capabilities=frozenset({'battle_page_v1'}))
            response = {'schema_version': 'nte-analysis-response-v1', 'engine_version': '0.3.0',
                        'batch_kind': 'battle_page_v1', 'dataset_version': 'fixture',
                        'account_id': 'fixture', 'generation': 9, 'battle_record_id': 7, 'result': {}}
            with patch.object(client, '_run', return_value=json.dumps(response).encode()):
                with self.assertRaises(NativeAnalysisError):
                    client.load_battle_page({'account_id': 'fixture', 'generation': 8, 'battle_record_id': 7})


if __name__ == '__main__':
    unittest.main()
