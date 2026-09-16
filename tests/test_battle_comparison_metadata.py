# 验证双路配对元数据只写入 Calc 记录信封，保留原始摘要、逐击和单路行为。
from copy import deepcopy
import unittest

from src.observability import OperationContext
from src.services.battle_capture_metadata import with_comparison_metadata
from src.services.battle_capture_service import BattleCaptureService
from tests.test_battle_capture_axis_service import _Core, _Writer, _wait_until
from tests.test_battle_native_capture_lifecycle import _NativeCore


class ComparisonMetadataTests(unittest.TestCase):
    def test_metadata_copies_the_record_without_changing_core_evidence(self):
        record = {'summary': {'total_damage': 100}, 'native_capture': {'reason': 'user_stop'}}
        original = deepcopy(record)
        enriched = with_comparison_metadata(record, comparison_id='pair', source='native')
        self.assertEqual(original, record)
        self.assertEqual(original['summary'], enriched['summary'])
        self.assertEqual(original['native_capture'], enriched['native_capture'])
        self.assertEqual({'comparison_id': 'pair', 'source': 'native'}, enriched['calc_capture'])
        self.assertEqual(original, with_comparison_metadata(record, comparison_id=None, source=None))

    def test_service_writes_shared_id_with_verified_lane_and_unique_operations(self):
        operations = []
        for source, core in (('native', _NativeCore(auto_end=False)), ('packet', _Core())):
            with self.subTest(source=source):
                writer = _Writer()
                operation = OperationContext.create('battle_report')
                operations.append(operation.operation_id)
                service = BattleCaptureService(operation_guard=lambda _: None,
                    client_factory=lambda: core, summary_writer=writer,
                    operation_context=operation, comparison_id='shared-operation', required_source=source,
                )
                service.start()
                self.addCleanup(service.close, timeout=2)
                self.assertTrue(core.capture_started.wait(1))
                if source == 'packet':
                    self.assertTrue(_wait_until(lambda: core.record_requests > 0))
                service.request_stop()
                self.assertTrue(_wait_until(lambda: not service.is_running))
                self.assertEqual('saved', service.state.persistence_status)
                self.assertEqual({'comparison_id': 'shared-operation', 'source': source},
                                 writer.record['calc_capture'])
                self.assertNotIn('calc_capture', writer.record['summary'])
                self.assertNotIn('calc_capture', core.get_battle_record())
        self.assertEqual(2, len(set(operations)))

    def test_single_capture_has_no_comparison_metadata(self):
        core, writer = _NativeCore(), _Writer()
        service = BattleCaptureService(operation_guard=lambda _: None,
            client_factory=lambda: core, summary_writer=writer,
            operation_context=OperationContext.create('battle_report'),
            required_source='native',
        )
        service.start()
        self.addCleanup(service.close, timeout=2)
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual('saved', service.state.persistence_status)
        self.assertNotIn('calc_capture', writer.record)

    def test_comparison_without_final_record_fails_and_discards_staging(self):
        class MissingRecord(_NativeCore):
            def get_battle_record(self, **kwargs):
                return None

            def get_battle_summary(self, **kwargs):
                raise AssertionError('paired capture must not fall back to summary')

        core, writer = MissingRecord(), _Writer()
        service = BattleCaptureService(operation_guard=lambda _: None,
            client_factory=lambda: core, summary_writer=writer,
            operation_context=OperationContext.create('battle_report'),
            comparison_id='shared-operation', required_source='native',
        )
        service.start()
        self.addCleanup(service.close, timeout=2)
        self.assertTrue(_wait_until(lambda: not service.is_running))
        self.assertEqual('error', service.state.phase)
        self.assertIn('缺少最终战斗记录', service.state.error)
        self.assertIsNone(writer.record)
        self.assertTrue(writer.discarded)

    def test_pairing_requires_explicit_source(self):
        with self.assertRaises(ValueError):
            BattleCaptureService(operation_guard=lambda _: None, client_factory=_Core, comparison_id='pair',
                operation_context=OperationContext.create('battle_report'))


if __name__ == '__main__':
    unittest.main()
