# 验证原生派生快照原样存储、失效不写，以及导出使用正式页面入口。
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.integrations.native_battle_page_wire import decode_derived_snapshot
from src.integrations.nte_analysis_core import NativeAnalysisError
from src.services.battle_inferred_target_condition_service import BattleInferredEncounter
from src.services.battle_native_page_service import BattleNativePageService
from src.services.battle_native_snapshot_persistence import persist_native_snapshot
from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadRequest
from src.services.battle_report_persistence_service import BattleReportPersistenceDependencies
from src.services.battle_report_transfer_service import BattleReportTransferService
from src.services.battle_report_history_entry_service import list_history_entries
from src.services.battle_inferred_target_snapshot_service import BattleInferredTargetSnapshotService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_report_transfer_dao import _insert_summary, _target_condition


def snapshot(record_id):
    inferred = BattleInferredEncounter('outer_realm', 'fixture|1|mixed', '公开样例环境',
        'fixture', '高', '公开样例证据', '', 1, None, (), (), (), None,
        residual_fit_score=0.25, residual_fit_gap=0.1)
    payload = json.loads(json.dumps(asdict(inferred)))
    return {'battle_record_id': record_id, 'payload_schema_version': 1,
            'static_dataset_id': 'fixture-dataset', 'static_schema_version': 31,
            'inference_status': 'resolved', 'inferred_payload': payload,
            **{key: payload[key] for key in ('algorithm_version', 'environment_kind',
               'environment_ref', 'environment_name', 'source_kind', 'confidence')}}


class NativeBattleSnapshotPersistenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.static = root / 'static.sqlite3'
        self.static.touch()
        self.semantics = root / 'semantics.json'
        self.semantics.write_text('{}', encoding='utf8')
        self.dependencies = BattleReportPersistenceDependencies('fixture', root / 'user.sqlite3', 2, self.static)
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            self.record_id = _insert_summary(dao, 'native-derived-fixture', 12.0)
            self.original = dao.load_battle_record(self.record_id)
        self.row = snapshot(self.record_id)

    def test_exact_payload_saved_and_repeated_result_does_not_write(self):
        checked = decode_derived_snapshot(self.row, battle_record_id=self.record_id,
                                          dataset_version='fixture-dataset')
        persist_native_snapshot(checked, dependencies=self.dependencies, checkpoint=lambda: None)
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            saved = dao.load_battle_inferred_target_snapshot(self.record_id)
            self.assertEqual(saved['inferred_payload'], self.row['inferred_payload'])
            self.assertEqual(dao.load_battle_record(self.record_id), self.original)
        with patch.object(UserDataDao, 'save_battle_inferred_target_snapshot') as write:
            persist_native_snapshot(checked, dependencies=self.dependencies, checkpoint=lambda: None)
            write.assert_not_called()

    def test_empty_metadata_labels_do_not_cause_repeated_writes(self):
        row = deepcopy(self.row)
        row['environment_name'] = row['inferred_payload']['environment_name'] = ''
        persist_native_snapshot(row, dependencies=self.dependencies, checkpoint=lambda: None)
        with patch.object(UserDataDao, 'save_battle_inferred_target_snapshot') as write:
            persist_native_snapshot(row, dependencies=self.dependencies, checkpoint=lambda: None)
            write.assert_not_called()

    def test_history_displays_native_revision_without_reusing_it_as_legacy_formula_input(self):
        for version in ('battle-encounter-native-first-v9', 'future-native-revision'):
            row = deepcopy(self.row)
            row['algorithm_version'] = row['inferred_payload']['algorithm_version'] = version
            checked = decode_derived_snapshot(row, battle_record_id=self.record_id,
                                              dataset_version='fixture-dataset')
            persist_native_snapshot(checked, dependencies=self.dependencies, checkpoint=lambda: None)
            with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
                entries = list_history_entries(user_dao=dao, static_dataset_id='fixture-dataset',
                                               static_schema_version=31)
                self.assertEqual(entries[0].environment_name, row['environment_name'])
                self.assertEqual(entries[0].environment_source, 'inferred')
                self.assertEqual(dao.load_battle_record(self.record_id), self.original)
                saved = dao.load_battle_inferred_target_snapshot(self.record_id)
                self.assertIsNone(BattleInferredTargetSnapshotService.restore(saved))
        for key, value in (('payload_schema_version', 2), ('static_dataset_id', 'other'),
                           ('static_schema_version', 999), ('inference_status', 'unresolved')):
            self.assertFalse(BattleInferredTargetSnapshotService.is_readable_row(
                {**row, key: value}, static_dataset_id='fixture-dataset', static_schema_version=31))

    def test_bad_identity_version_or_payload_rejected(self):
        for key, value in [('battle_record_id', self.record_id + 1), ('payload_schema_version', 2),
                           ('static_dataset_id', 'other'), ('algorithm_version', 'other'),
                           ('environment_name', 'different')]:
            row = {**self.row, key: value}
            with self.subTest(key=key), self.assertRaises(NativeAnalysisError):
                decode_derived_snapshot(row, battle_record_id=self.record_id, dataset_version='fixture-dataset')
        row = deepcopy(self.row)
        row['inferred_payload']['extra'] = 1
        with self.assertRaises(NativeAnalysisError):
            decode_derived_snapshot(row, battle_record_id=self.record_id, dataset_version='fixture-dataset')

    def test_user_confirmation_supersedes_pending_inference(self):
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            dao.save_battle_target_condition(self.record_id, _target_condition())
        persist_native_snapshot(self.row, dependencies=self.dependencies, checkpoint=lambda: None)
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            self.assertIsNone(dao.load_battle_inferred_target_snapshot(self.record_id))

    def test_cancellation_after_open_propagates_without_write(self):
        checkpoint = Mock(side_effect=[None, CancelledError()])
        with self.assertRaises(CancelledError):
            persist_native_snapshot(self.row, dependencies=self.dependencies, checkpoint=checkpoint)
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            self.assertIsNone(dao.load_battle_inferred_target_snapshot(self.record_id))

    def test_save_failure_is_best_effort(self):
        with patch.object(UserDataDao, 'save_battle_inferred_target_snapshot', side_effect=RuntimeError('fixture')), \
                patch('src.services.battle_native_snapshot_persistence.log_event') as log:
            persist_native_snapshot(self.row, dependencies=self.dependencies, checkpoint=lambda: None)
            log.assert_called_once()

    def test_service_persists_native_result_after_final_validation(self):
        client = Mock(dataset_version='fixture-dataset', load_battle_page=Mock(return_value={'derived_snapshot': self.row}))
        service = BattleNativePageService(client=client, dependencies=self.dependencies,
                                          semantics_path=self.semantics, context_is_current=lambda _: True)
        with patch('src.services.battle_native_page_service.decode_page', return_value='rendered'):
            self.assertEqual(service.load(BattleReportAnalysisLoadRequest(self.record_id)), 'rendered')
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            self.assertEqual(dao.load_battle_inferred_target_snapshot(self.record_id)['inferred_payload'], self.row['inferred_payload'])

    def test_stale_service_does_not_persist(self):
        current = True
        def run(*args, **kwargs):
            nonlocal current
            current = False
            return {'derived_snapshot': self.row}
        service = BattleNativePageService(client=Mock(load_battle_page=run), dependencies=self.dependencies,
                                          semantics_path=self.semantics, context_is_current=lambda _: current)
        with patch('src.services.battle_native_page_service.decode_page', return_value='stale'):
            with self.assertRaises(CancelledError):
                service.load(BattleReportAnalysisLoadRequest(self.record_id))
        with UserDataDao(self.dependencies.user_database_path, account_id='fixture') as dao:
            self.assertIsNone(dao.load_battle_inferred_target_snapshot(self.record_id))

    def test_export_analysis_uses_native_overview_entry(self):
        native = Mock(load=Mock(return_value=SimpleNamespace(analysis=None)))
        history = SimpleNamespace(native_page_loader=native, load_analysis=Mock(side_effect=AssertionError('old path')))
        service = object.__new__(BattleReportTransferService)
        service._history_service = history
        service._dependencies = self.dependencies
        missing = []
        value = service._analysis_projection(self.record_id, missing)
        self.assertIsNone(value['target_inference'])
        request = native.load.call_args.args[0]
        self.assertEqual(request.detail_level, 'overview')
        self.assertEqual(request.battle_record_id, self.record_id)
        history.load_analysis.assert_not_called()
