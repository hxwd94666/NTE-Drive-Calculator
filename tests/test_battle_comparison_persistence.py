# 验证两路采集并发写入同一临时账号库时，操作身份、原始轴和冻结战报保持独立。
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
from unittest.mock import patch

from src.observability import OperationContext
from src.services.battle_capture_metadata import with_comparison_metadata
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies, BattleReportPersistenceService,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_axis_dao import _profile, _snapshot
from tests.test_battle_report_persistence_service import _summary


class ComparisonPersistenceTests(unittest.TestCase):
    def test_concurrent_writers_keep_operation_records_and_raw_axes_independent(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fixture.sqlite3'
            with UserDataDao(path, account_id='fixture') as dao:
                dao.import_inventory_snapshot(_snapshot(1, []))
            barrier = Barrier(2, timeout=15)
            comparison_id = 'fixture-native-operation'
            fixtures = {}
            for lane, damage in (('native', 100.0), ('packet', 90.0)):
                operation_id = f'fixture-{lane}-operation'
                source_record = f'fixture-{lane}-record'
                row = {
                    'sequence': '1', 'character_id': 1072, 'character_known': True,
                    'character_name': '测试角色', 'direction': 'outgoing',
                    'damage': damage, 'total_damage': damage, 'max_hp_reduction': None,
                    'follow_up_labels': [],
                }
                if lane == 'native':
                    row['native_capture'] = {
                        'providerId': 'fixture-provider', 'captureId': 'fixture-native',
                        'hitId': '1', 'rawHit': {'attackerEffects': None,
                            'victimEffects': {'complete': True, 'ended': False,
                                'effects': [{'key': '/Game/Fixture/GE_Buff', 'stacks': 3}]}},
                    }
                base = _summary(total_damage=damage, total_hits=1)
                summary = replace(base, characters=(replace(base.characters[0],
                    character_id=1072, damage=damage, hits=1),))
                payload = {'total_damage': damage, 'total_hits': 1,
                           'characters': [{'char_id': 1072, 'damage': damage}]}
                fixtures[lane] = (operation_id, source_record, row, summary, payload)

            def persist(lane):
                operation_id, source_record, row, summary, payload = fixtures[lane]
                service = BattleReportPersistenceService(
                    dependencies=BattleReportPersistenceDependencies('fixture', path, 1,
                        Path(temporary) / 'static-fixture.sqlite3'),
                    context_is_current=lambda _: True,
                    operation_context=OperationContext.create('battle_report', account_id='fixture'),
                )
                # Each barrier releases two independent connections into the same writer stage.
                barrier.wait()
                service.begin_capture(capture_operation_id=operation_id,
                    captured_at_utc='2026-09-08T00:00:00+00:00')
                barrier.wait()
                service.append_axis_page(capture_operation_id=operation_id, page={
                    'contract_version': 5, 'battle_record_id': source_record, 'generation': '1',
                    'complete': True, 'first_available_cursor': '1', 'next_cursor': None,
                    'total_hits': '1', 'retained_hits': 1, 'rows': [row],
                })
                barrier.wait()
                return service.finalize_summary(
                    raw_summary_payload=payload, summary=summary,
                    capture_operation_id=operation_id, captured_at_utc='2026-09-08T00:00:00+00:00',
                    finalized_at_utc='2026-09-08T00:00:10+00:00',
                    raw_record_payload=with_comparison_metadata(
                        {'battle_record_id': source_record, 'axis_complete': True,
                         'time_stop_intervals': []},
                        comparison_id=comparison_id, source=lane,
                    ),
                )

            module = 'src.services.battle_report_persistence_service'
            # Only static growth calculation is stubbed; all user DB connections and transactions are real.
            with patch(module + '.StaticGameDataDao') as static, \
                    patch.object(BattleReportPersistenceService, '_load_effective_profiles',
                        return_value={1072: _profile(1072, 'fork_Test')}), \
                    patch.object(BattleReportPersistenceService, '_resolve_character_stat_snapshots',
                        return_value={}):
                static.return_value.__enter__.return_value.summary.return_value = {
                    'dataset': {'dataset_id': 'fixture'}, 'schema_version': 32}
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = {lane: executor.submit(persist, lane) for lane in fixtures}
                    outcomes = {lane: future.result(timeout=30) for lane, future in futures.items()}

            self.assertEqual(2, len({value.battle_record_id for value in outcomes.values()}))
            with UserDataDao(path) as dao:
                persisted_pair = {}
                for lane, outcome in outcomes.items():
                    operation_id, _, expected_row, summary, payload = fixtures[lane]
                    self.assertEqual('saved', outcome.status)
                    state = dao.battle_axis_capture_state(operation_id)
                    self.assertEqual('finalized', state['capture_state'])
                    self.assertEqual(1, state['stored_hits'])
                    self.assertEqual(outcome.battle_record_id, state['battle_record_id'])
                    record = dao.load_battle_record(outcome.battle_record_id)
                    self.assertEqual(operation_id, record['capture_operation_id'])
                    self.assertEqual(payload, record['raw_summary_payload'])
                    self.assertEqual(summary.total_damage, record['total_damage'])
                    raw_record = dao._db().execute(
                        'SELECT raw_record_json FROM battle_axis_capture WHERE capture_id = ?',
                        (state['capture_id'],),
                    ).fetchone()
                    metadata = json.loads(raw_record['raw_record_json'])['calc_capture']
                    self.assertEqual({'comparison_id': comparison_id, 'source': lane}, metadata)
                    persisted_pair[metadata['source']] = outcome.battle_record_id
                    stored = dao._db().execute(
                        'SELECT raw_hit_json FROM battle_hit_evidence WHERE capture_id = ?',
                        (state['capture_id'],),
                    ).fetchall()
                    self.assertEqual([expected_row], [json.loads(hit['raw_hit_json']) for hit in stored])
                    build = dao.load_battle_build_snapshot(outcome.battle_record_id)
                    self.assertEqual([1072], [row['character_id'] for row in build['characters']])
                self.assertEqual({'native', 'packet'}, set(persisted_pair))
                self.assertEqual(2, len(set(persisted_pair.values())))


if __name__ == '__main__':
    unittest.main()
