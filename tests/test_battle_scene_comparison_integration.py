# 使用真实采集服务、Qt 协调和临时账号库验证切场保存与下一对续录顺序。
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from src.features.battle_report.comparison_capture import ComparisonCapture
from src.features.battle_report.controller import BattleReportController
from src.observability import OperationContext
from src.services.battle_capture_service import BattleCaptureService
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies, BattleReportPersistenceService,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_battle_axis_dao import _profile, _snapshot
from tests.test_battle_capture_axis_service import _Core, _summary_payload
from tests.test_battle_native_capture_lifecycle import _NativeCore
from tests.test_battle_report_controller import _Overlay, _Page, _controller_state


class _DelayedPacketCore(_Core):
    def __init__(self):
        super().__init__()
        self.stop_entered = Event()
        self.release_stop = Event()

    def stop_capture(self):
        self.stop_entered.set()
        if not self.release_stop.wait(5):
            raise RuntimeError('fixture_packet_stop_not_released')
        return super().stop_capture()


class SceneComparisonIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.005)
        self.fail('fixture lifecycle did not reach the expected boundary')

    def test_scene_end_saves_both_sources_before_controller_requests_next_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'fixture.sqlite3'
            with UserDataDao(path, account_id='fixture') as dao:
                dao.import_inventory_snapshot(_snapshot(1, []))
            native, packet = _NativeCore(auto_end=False), _DelayedPacketCore()
            services = {}
            operations = {}
            for lane, core in (('native', native), ('packet', packet)):
                operation = OperationContext.create('battle_report', account_id='fixture')
                operations[lane] = operation.operation_id
                writer = BattleReportPersistenceService(
                    dependencies=BattleReportPersistenceDependencies('fixture', path, 1,
                        Path(temporary) / 'static-fixture.sqlite3'),
                    context_is_current=lambda _: True, operation_context=operation,
                )
                services[lane] = BattleCaptureService(operation_guard=lambda _: None,
                    client_factory=lambda client=core: client, summary_writer=writer,
                    operation_context=operation, required_source=lane,
                    comparison_id='fixture-pair-one',
                )
            owner = ComparisonCapture(services, defer_packet_start=True)
            overlay = _Overlay()
            overlay.update_summary = lambda _: None
            page = _Page()
            controller = _controller_state(overlay=overlay, page=page, service=owner)
            next_pairs = []

            def next_pair(**kwargs):
                with UserDataDao(path) as dao:
                    persisted = [dao.battle_axis_capture_state(op) for op in operations.values()]
                next_pairs.append((kwargs, owner.comparison, persisted))

            controller.start = next_pair
            owner.add_state_handler(lambda state: BattleReportController._apply_state(controller, 7, state))
            module = 'src.services.battle_report_persistence_service'
            with patch(module + '.StaticGameDataDao') as static, \
                    patch.object(BattleReportPersistenceService, '_load_effective_profiles',
                        return_value={1072: _profile(1072, 'fork_Test')}), \
                    patch.object(BattleReportPersistenceService, '_resolve_character_stat_snapshots',
                        return_value={}):
                static.return_value.__enter__.return_value.summary.return_value = {
                    'dataset': {'dataset_id': 'fixture'}, 'schema_version': 32}
                try:
                    owner.start()
                    self.wait_for(lambda: owner.state.phase == 'running')
                    # Real service callback supplies packet evidence before native announces scene end.
                    for handler in tuple(packet.handlers):
                        handler({'method': 'event.battle.summary',
                                 'params': {**_summary_payload(), 'sequence': 1}})
                    native.end('scene_transition')
                    self.wait_for(lambda: packet.stop_entered.is_set()
                                  and owner.comparison.native.phase == 'stopped')
                    self.assertEqual('saved', owner.comparison.native.persistence_status)
                    self.assertFalse(owner.comparison.finished)
                    self.assertEqual([], next_pairs)
                    self.assertFalse(any(state.phase in {'stopped', 'error'} for state in page.states))
                    packet.release_stop.set()
                    self.wait_for(lambda: bool(next_pairs))
                    self.assertEqual(1, len(next_pairs))
                    kwargs, final_pair, persisted = next_pairs[0]
                    self.assertEqual({'preserve_inventory_pause': True,
                                      'continue_after_scene': True}, kwargs)
                    self.assertTrue(final_pair.finished)
                    self.assertFalse(final_pair.interrupted)
                    self.assertEqual({'finalized'}, {row['capture_state'] for row in persisted})
                    self.assertEqual(2, len({row['battle_record_id'] for row in persisted}))
                    with UserDataDao(path) as dao:
                        for lane, operation_id in operations.items():
                            row = dao._db().execute(
                                'SELECT raw_record_json, battle_record_id FROM battle_axis_capture '
                                'WHERE capture_operation_id = ?', (operation_id,),
                            ).fetchone()
                            raw_record = json.loads(row['raw_record_json'])
                            self.assertEqual({'comparison_id': 'fixture-pair-one', 'source': lane},
                                             raw_record['calc_capture'])
                            record = dao.load_battle_record(row['battle_record_id'])
                            self.assertEqual(10, record['total_damage'])
                            self.assertEqual(1, record['total_hits'])
                finally:
                    packet.release_stop.set()
                    owner.close(timeout=2)


if __name__ == '__main__':
    unittest.main()
