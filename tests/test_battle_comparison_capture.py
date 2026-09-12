# 验证固定双路 Qt 协调的启动、停止、部分保存及无需事件分发的关闭边界。
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QObject
from PySide6.QtWidgets import QApplication

from src.domain.battle_report import BattleCaptureState, EMPTY_BATTLE_CAPTURE_STATE
from src.features.battle_report.comparison_capture import ComparisonCapture
from tests.test_battle_report_persistence_service import _summary


class _Lane:
    def __init__(self, name, actions, *, start_error=False):
        self.name, self.actions = name, actions
        self.start_error = start_error
        self.handlers = []
        self.state = EMPTY_BATTLE_CAPTURE_STATE
        self.is_running = False
        self.on_stop = None
        self.close_timeouts = []

    def add_state_handler(self, handler):
        self.handlers.append(handler)

    def remove_state_handler(self, handler):
        self.handlers.remove(handler)

    def start(self):
        self.actions.append(('start', self.name))
        if self.start_error:
            raise RuntimeError('fixture_start_failed')
        self.is_running = True
        self.emit(BattleCaptureState('starting', '启动中', True))

    def emit(self, state):
        self.state = state
        self.is_running = state.running
        for handler in tuple(self.handlers):
            handler(state)

    def ready(self):
        self.emit(BattleCaptureState('running', '采集中', True, summary=_summary()))

    def request_stop(self, *, end_reason=None):
        self.actions.append(('stop', self.name))
        if end_reason:
            self.actions.append(('reason', self.name, end_reason))
        if self.on_stop is not None:
            self.on_stop()

    def request_discard(self):
        self.actions.append(('discard', self.name))

    def finish(self, *, record_id=None, error=None, discarded=False):
        self.emit(BattleCaptureState(
            phase='error' if error else 'stopped', running=False,
            message=error or '结束并保存', error=error, summary=_summary(),
            battle_record_id=record_id,
            persistence_status='discarded_restart' if discarded else 'saved' if record_id else 'not_requested',
            retention_kind='auto' if record_id else None,
        ))

    def close(self, *, timeout):
        self.actions.append(('close', self.name))
        self.close_timeouts.append(timeout)
        # Write final public state without invoking callbacks: Qt may be blocked in close().
        self.state = BattleCaptureState('stopped', '关闭并保存', False,
            battle_record_id=11 if self.name == 'native' else 22, persistence_status='saved')
        self.is_running = False


class ComparisonCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def owner(self, **packet_options):
        self.actions = []
        self.native = _Lane('native', self.actions)
        self.packet = _Lane('packet', self.actions, **packet_options)
        owner = ComparisonCapture({'native': self.native, 'packet': self.packet})
        self.addCleanup(owner.close, timeout=0)
        self.states = []
        owner.add_state_handler(self.states.append)
        owner.start()
        self.app.processEvents()
        return owner

    def test_queued_states_wait_for_both_lanes_before_running(self):
        owner = self.owner()
        self.native.ready()
        self.assertEqual('starting', owner.comparison.native.phase)
        self.app.processEvents()
        self.assertEqual('starting', owner.state.phase)
        self.packet.ready()
        self.app.processEvents()
        self.assertEqual('running', owner.state.phase)
        self.assertTrue(owner.is_running)

    def test_user_stop_intent_precedes_synchronous_child_terminal_callbacks(self):
        owner = self.owner()
        self.native.on_stop = lambda: self.native.finish(record_id=11)
        self.packet.on_stop = lambda: self.packet.finish(record_id=22)
        owner.request_stop()
        self.app.processEvents()
        self.assertFalse(owner.comparison.interrupted)
        self.assertTrue(owner.comparison.finished)
        self.assertEqual(11, owner.state.battle_record_id)
        self.assertEqual(22, owner.comparison.packet.battle_record_id)

    def test_spontaneous_end_stops_other_lane_but_waits_for_its_terminal(self):
        owner = self.owner()
        self.native.finish(record_id=11)
        self.app.processEvents()
        self.assertIn(('stop', 'packet'), self.actions)
        self.assertTrue(owner.comparison.interrupted)
        self.assertFalse(owner.comparison.finished)
        self.assertTrue(owner.state.running)
        self.packet.finish(record_id=22)
        self.app.processEvents()
        self.assertIn('对照不完整', owner.state.message)
        self.assertEqual(11, owner.state.battle_record_id)
        self.assertEqual([], self.native.handlers)
        self.assertEqual([], self.packet.handlers)

    def test_native_failure_keeps_packet_record_and_labels_lane_error(self):
        owner = self.owner()
        self.native.finish(error='fixture_native_error')
        self.app.processEvents()
        self.packet.finish(record_id=22)
        self.app.processEvents()
        self.assertEqual('error', owner.state.phase)
        self.assertEqual(22, owner.state.battle_record_id)
        self.assertIn('DLL：fixture_native_error', owner.state.error)
        self.assertEqual('saved', owner.state.persistence_status)

    def test_second_lane_start_failure_stops_first_without_early_master_terminal(self):
        owner = self.owner(start_error=True)
        self.assertIn(('stop', 'native'), self.actions)
        self.assertFalse(owner.comparison.finished)
        self.native.finish(record_id=11)
        self.app.processEvents()
        self.assertEqual('error', owner.state.phase)
        self.assertEqual(11, owner.state.battle_record_id)
        self.assertIn('抓包', owner.state.error)

    def test_close_requests_both_stops_and_shares_deadline_without_dispatch(self):
        owner = self.owner()
        self.actions.clear()
        with patch('src.features.battle_report.comparison_capture.time.monotonic',
                   side_effect=[100.0, 100.0, 105.0]):
            owner.close(timeout=12)
        self.assertEqual([('stop', 'native'), ('stop', 'packet'),
                          ('close', 'native'), ('close', 'packet')], self.actions)
        self.assertEqual([12.0], self.native.close_timeouts)
        self.assertEqual([7.0], self.packet.close_timeouts)
        self.assertTrue(owner.comparison.finished)
        self.assertFalse(owner.is_running)
        self.assertEqual(11, owner.state.battle_record_id)

    def test_discard_both_exposes_existing_rerecord_contract(self):
        owner = self.owner()
        owner.request_discard()
        self.assertIn(('discard', 'native'), self.actions)
        self.assertIn(('discard', 'packet'), self.actions)
        self.native.finish(discarded=True)
        self.packet.finish(discarded=True)
        self.app.processEvents()
        self.assertEqual('discarded_restart', owner.state.persistence_status)
        self.assertFalse(owner.comparison.interrupted)

    def test_old_queued_state_cannot_reopen_finished_lane(self):
        owner = self.owner()
        late = tuple(self.native.handlers)
        self.native.finish(record_id=11)
        self.app.processEvents()
        for handler in late:
            handler(replace(self.native.state, phase='running', running=True))
        self.app.processEvents()
        self.assertEqual('stopped', owner.comparison.native.phase)
        self.packet.finish(record_id=22)
        self.app.processEvents()
        self.assertEqual(1, sum(state.phase in ('stopped', 'error') for state in self.states))

    def test_fixed_sources_require_distinct_owners(self):
        lane = _Lane('native', [])
        for mapping in ({'native': lane}, {'native': lane, 'packet': lane}):
            with self.assertRaises(ValueError):
                ComparisonCapture(mapping)

    def test_scene_boundary_stops_packet_before_native_finishes_persisting(self):
        owner = self.owner()
        self.native.emit(BattleCaptureState(
            'stopping', '切场', True, end_reason='scene_transition'))
        self.app.processEvents()
        self.assertIn(('stop', 'packet'), self.actions)
        self.assertIn(('reason', 'packet', 'scene_transition'), self.actions)
        self.assertFalse(owner.comparison.finished)
        self.assertFalse(owner.comparison.interrupted)
        self.packet.finish(record_id=22)
        self.app.processEvents()
        self.assertTrue(owner.is_running)
        self.native.emit(BattleCaptureState('stopped', '已保存', False,
            battle_record_id=11, persistence_status='saved', end_reason='scene_transition'))
        self.app.processEvents()
        self.assertEqual('scene_transition', owner.state.end_reason)
        self.assertEqual(22, owner.comparison.packet.battle_record_id)
        self.assertFalse(owner.comparison.interrupted)

    def test_scene_resume_waits_for_native_readiness_before_starting_packet(self):
        actions = []
        native, packet = _Lane('native', actions), _Lane('packet', actions)
        owner = ComparisonCapture({'native': native, 'packet': packet}, defer_packet_start=True)
        self.addCleanup(owner.close, timeout=0)
        owner.start()
        self.app.processEvents()
        self.assertEqual([('start', 'native')], actions)
        native.ready()
        self.app.processEvents()
        self.assertIn(('start', 'packet'), actions)
        self.assertEqual('starting', owner.state.phase)
        packet.ready()
        self.app.processEvents()
        self.assertEqual('running', owner.state.phase)

    def test_stop_while_loading_never_starts_packet_on_late_ready(self):
        actions = []
        native, packet = _Lane('native', actions), _Lane('packet', actions)
        owner = ComparisonCapture({'native': native, 'packet': packet}, defer_packet_start=True)
        self.addCleanup(owner.close, timeout=0)
        owner.start()
        owner.request_stop()
        native.ready()
        native.finish()
        self.app.processEvents()
        self.assertNotIn(('start', 'packet'), actions)
        self.assertTrue(owner.comparison.finished)

    def test_discard_while_loading_finishes_without_unstarted_packet_worker(self):
        actions = []
        native, packet = _Lane('native', actions), _Lane('packet', actions)
        owner = ComparisonCapture({'native': native, 'packet': packet}, defer_packet_start=True)
        self.addCleanup(owner.close, timeout=0)
        owner.start()
        owner.request_discard()
        native.finish(discarded=True)
        self.app.processEvents()
        self.assertTrue(owner.comparison.finished)
        self.assertEqual('discarded_restart', owner.state.persistence_status)
        self.assertNotIn(('start', 'packet'), actions)

    def test_finished_pairs_release_parent_children_after_final_publication(self):
        parent = QObject()
        finals = []
        for _ in range(3):
            native, packet = _Lane('native', []), _Lane('packet', [])
            owner = ComparisonCapture({'native': native, 'packet': packet}, parent=parent)
            owner.comparison_changed.connect(finals.append)
            owner.start()
            owner.request_stop()
            native.finish(record_id=11)
            packet.finish(record_id=22)
            self.app.processEvents()
            self.assertTrue(finals[-1].finished)
            self.assertEqual(22, finals[-1].packet.battle_record_id)
            self.assertEqual([], native.handlers)
            self.assertEqual([], packet.handlers)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertEqual([], parent.children())


if __name__ == '__main__':
    unittest.main()
