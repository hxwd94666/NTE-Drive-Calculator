# 验证未知角色逐击原样落库，正式角色索引与养成快照只接收正 ID。
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.domain.battle_report import BattleAbyssHalfSummary, BattleAbyssSummary
from src.observability import OperationContext
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies, BattleReportPersistenceService,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_axis_dao import _profile, _snapshot
from tests.test_battle_report_persistence_service import _summary


class UnknownCharacterPersistenceTests(unittest.TestCase):
    def _persist(self, *, known: bool) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fixture.sqlite3'
            with UserDataDao(path, account_id='fixture') as dao:
                dao.import_inventory_snapshot(_snapshot(1, []))
            service = BattleReportPersistenceService(
                dependencies=BattleReportPersistenceDependencies('fixture', path, 1,
                    Path(temporary) / 'static-fixture.sqlite3'),
                context_is_current=lambda _: True,
                operation_context=OperationContext.create('battle_report', account_id='fixture'),
            )
            module = 'src.services.battle_report_persistence_service'
            with patch(module + '.StaticGameDataDao') as static, \
                    patch.object(service, '_load_effective_profiles', return_value={1072: _profile(1072, 'fork_Test')}), \
                    patch.object(service, '_resolve_character_stat_snapshots', return_value={}) as stats:
                static.return_value.__enter__.return_value.summary.return_value = {
                    'dataset': {'dataset_id': 'fixture'}, 'schema_version': 32}
                service.begin_capture(capture_operation_id='fixture-capture',
                                      captured_at_utc='2026-09-08T00:00:00+00:00')
            self.assertEqual((1072,), stats.call_args.kwargs['character_ids'])
            ids = [0, 1072] if known else [0]
            rows = [{
                'sequence': str(index), 'character_id': character_id,
                'character_known': character_id > 0, 'character_name': '测试角色' if character_id else '未知角色',
                'direction': 'outgoing', 'damage': 100.0, 'total_damage': 100.0,
                'max_hp_reduction': None, 'follow_up_labels': [],
                'native_capture': {'providerId': 'fixture-provider', 'captureId': 'fixture-capture',
                    'hitId': str(index), 'rawHit': {'attackerEffects': None,
                    'victimEffects': {'complete': True, 'ended': False, 'effects': [
                        {'key': '/Game/Fixture/GE_Buff', 'stacks': 3, 'inhibited': False}]}}},
            } for index, character_id in enumerate(ids, 1)]
            service.append_axis_page(capture_operation_id='fixture-capture', page={
                'contract_version': 5, 'battle_record_id': 'fixture-record', 'generation': '1',
                'complete': True, 'first_available_cursor': '1', 'next_cursor': None,
                'total_hits': str(len(rows)), 'retained_hits': len(rows), 'rows': rows,
            })
            base = _summary(total_damage=100.0 * len(rows), total_hits=len(rows))
            characters = tuple(replace(base.characters[0], character_id=cid, hits=1,
                                       damage=100.0) for cid in ids)
            # Both global and half summaries may repeat the unknown bucket.
            summary = replace(base, characters=characters, abyss=BattleAbyssSummary(
                detected=True, first_half=BattleAbyssHalfSummary('first', 10.0,
                    base.total_damage, base.total_dps, characters, ())))
            payload = {'total_damage': base.total_damage, 'total_hits': len(rows),
                       'characters': [{'char_id': cid, 'damage': 100.0} for cid in ids],
                       'native_capture': {'complete': False, 'sourceCoverage': 'unknown'}}
            outcome = service.finalize_summary(raw_summary_payload=payload, summary=summary,
                capture_operation_id='fixture-capture', captured_at_utc='2026-09-08T00:00:00+00:00',
                finalized_at_utc='2026-09-08T00:00:10+00:00',
                raw_record_payload={'battle_record_id': 'fixture-record', 'axis_complete': True,
                                    'time_stop_intervals': [], 'native_capture': payload['native_capture']})
            expected = (1072,) if known else ()
            self.assertEqual('saved', outcome.status)
            with UserDataDao(path) as dao:
                record = dao.load_battle_record(outcome.battle_record_id)
                build = dao.load_battle_build_snapshot(outcome.battle_record_id)
                self.assertEqual(expected, record['character_ids'])
                self.assertEqual(len(expected), record['character_count'])
                self.assertEqual(payload, record['raw_summary_payload'])
                self.assertEqual(base.total_damage, record['total_damage'])
                self.assertEqual(len(rows), record['total_hits'])
                self.assertEqual(list(expected), [c['character_id'] for c in build['characters']])
                stored = dao._db().execute('SELECT character_id, character_known, raw_hit_json '
                    'FROM battle_hit_evidence ORDER BY sequence_order').fetchall()
                self.assertEqual(rows, [json.loads(row['raw_hit_json']) for row in stored])
                self.assertIsNone(stored[0]['character_id'])
                self.assertEqual(0, stored[0]['character_known'])
                state = dao.battle_axis_capture_state('fixture-capture')
                self.assertEqual('finalized', state['capture_state'])
                self.assertEqual(len(rows), state['stored_hits'])

    def test_unknown_only_keeps_damage_and_native_buffs_without_formal_roles(self):
        self._persist(known=False)

    def test_mixed_known_unknown_keeps_all_hits_and_freezes_only_known_role(self):
        self._persist(known=True)


if __name__ == '__main__':
    unittest.main()
