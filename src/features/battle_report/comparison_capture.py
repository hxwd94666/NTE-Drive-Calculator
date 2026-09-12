# 在 Qt 线程协调固定 DLL 与抓包两路生命周期，各路继续独立采集和保存。
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
import time

from PySide6.QtCore import QObject, Qt, Signal, Slot

from src.domain.battle_capture_comparison import BattleCaptureComparisonState
from src.domain.battle_report import BattleCaptureState, EMPTY_BATTLE_CAPTURE_STATE
from src.services.battle_capture_service import BattleCaptureService


_LANES = ('native', 'packet')
_LABELS = {'native': 'DLL', 'packet': '抓包'}
_TERMINAL = frozenset(('stopped', 'error'))


class ComparisonCapture(QObject):
    """One-shot owner; child workers own processes and persistence, Qt owns pairing."""

    comparison_changed = Signal(object)
    _lane_changed = Signal(str, object)

    def __init__(self, services: Mapping[str, BattleCaptureService],
                 parent: QObject | None = None, *, defer_packet_start: bool = False) -> None:
        super().__init__(parent)
        if set(services) != set(_LANES) or services['native'] is services['packet']:
            raise ValueError('对照采集必须提供独立的 native 与 packet 两路')
        self._services = {lane: services[lane] for lane in _LANES}
        self._states = {lane: EMPTY_BATTLE_CAPTURE_STATE for lane in _LANES}
        self._handlers: list[Callable[[BattleCaptureState], None]] = []
        self._state = EMPTY_BATTLE_CAPTURE_STATE
        self._started = False
        self._finished = False
        self._stop_requested = False
        self._discard_requested = False
        self._interrupted = False
        self._scene_transition = False
        self._defer_packet_start = defer_packet_start
        self._started_lanes: set[str] = set()
        self._lane_changed.connect(self._accept_state, Qt.ConnectionType.QueuedConnection)
        # Retain exact callback objects so they can be removed after both owners finish.
        self._callbacks = {
            lane: (lambda state, source=lane: self._lane_changed.emit(source, state))
            for lane in _LANES
        }
        for lane in _LANES:
            self._services[lane].add_state_handler(self._callbacks[lane])

    @property
    def is_running(self) -> bool:
        return (self._started and not self._finished) or any(
            service.is_running for service in self._services.values())

    @property
    def state(self) -> BattleCaptureState:
        return self._state

    @property
    def comparison(self) -> BattleCaptureComparisonState:
        return BattleCaptureComparisonState(
            native=self._states['native'], packet=self._states['packet'],
            finished=self._finished, interrupted=self._interrupted,
            stop_requested=self._stop_requested,
        )

    def add_state_handler(self, handler: Callable[[BattleCaptureState], None]) -> None:
        if handler not in self._handlers:
            self._handlers.append(handler)

    def remove_state_handler(self, handler: Callable[[BattleCaptureState], None]) -> None:
        if handler in self._handlers:
            self._handlers.remove(handler)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._states = {lane: BattleCaptureState(
            phase='starting', message=f'正在启动{_LABELS[lane]}采集……', running=True,
        ) for lane in _LANES}
        self._publish()
        for index, lane in enumerate(_LANES):
            if lane == 'packet' and self._defer_packet_start:
                break
            try:
                self._started_lanes.add(lane)
                self._services[lane].start()
            except Exception as error:
                # A synchronous startup failure has no worker that will publish a terminal state.
                for unstarted in _LANES[index + 1:]:
                    self._states[unstarted] = BattleCaptureState(
                        phase='stopped', message='另一路启动失败，本路未启动。', running=False)
                self._accept_state(lane, BattleCaptureState(
                    phase='error', message=f'{_LABELS[lane]}启动失败。', running=False,
                    error=str(error), error_code=type(error).__name__))
                break

    def request_stop(self) -> None:
        if not self._started or self._finished:
            return
        self._stop_requested = True
        self._stop_lanes()
        self._publish()

    def _stop_lanes(self, *, except_lane: str | None = None) -> None:
        for lane, service in self._services.items():
            if lane == except_lane:
                continue
            if lane in self._started_lanes:
                if self._scene_transition:
                    service.request_stop(end_reason='scene_transition')
                else:
                    service.request_stop()
            else:
                self._states[lane] = BattleCaptureState(
                    'stopped', '尚未启动采集。', False, persistence_status='skipped_empty')

    def request_discard(self) -> None:
        if not self._started or self._finished:
            return
        self._discard_requested = True
        self._stop_requested = True
        for lane, service in self._services.items():
            if lane in self._started_lanes:
                service.request_discard()
            else:
                self._states[lane] = BattleCaptureState(
                    'stopped', '尚未启动，已放弃。', False, persistence_status='discarded_restart')
        self._publish()

    def close(self, *, timeout: float = 12.0) -> None:
        # Ask both first. Waiting for lane one must not extend lane two's capture window.
        self.request_stop()
        deadline = time.monotonic() + max(0.0, timeout)
        first_error: Exception | None = None
        for service in self._services.values():
            try:
                service.close(timeout=max(0.0, deadline - time.monotonic()))
            except Exception as error:
                if first_error is None:
                    first_error = error
        # close() may block the Qt dispatcher. Public child states are already synchronized.
        if self._started:
            for lane, service in self._services.items():
                self._accept_state(lane, service.state)
        if first_error is not None:
            raise first_error

    @Slot(str, object)
    def _accept_state(self, lane: str, state: object) -> None:
        if not self._started or self._finished or not isinstance(state, BattleCaptureState):
            return
        if self._states[lane].phase in _TERMINAL:
            return  # Already queued older running updates cannot reopen a finished lane.
        self._states[lane] = state
        if (lane == 'native' and state.end_reason == 'scene_transition'
                and state.phase in {'stopping', 'stopped'} and not self._stop_requested):
            self._scene_transition = True
            self._stop_requested = True
            self._stop_lanes(except_lane=lane)
        if (lane == 'native' and state.phase == 'running' and self._defer_packet_start
                and 'packet' not in self._started_lanes and not self._stop_requested):
            self._started_lanes.add('packet')
            try:
                self._services['packet'].start()
            except Exception as error:
                self._accept_state('packet', BattleCaptureState(
                    'error', '抓包启动失败。', False, error=str(error),
                    error_code=type(error).__name__))
        if state.phase in _TERMINAL:
            if state.phase == 'error' or not self._stop_requested:
                self._interrupted = True
            if not self._stop_requested:
                # Distinct from user intent: retain interrupted=True for the comparison panel.
                self._stop_requested = True
                self._stop_lanes(except_lane=lane)
            self._finished = all(s.phase in _TERMINAL for s in self._states.values())
            if self._finished:
                for source, service in self._services.items():
                    service.remove_state_handler(self._callbacks[source])
        self._publish()

        if self._finished:
            # Publish the immutable final view before releasing this one-shot Qt owner.
            self._callbacks.clear()
            self._handlers.clear()
            self.deleteLater()

    def _publish(self) -> None:
        native, packet = self._states['native'], self._states['packet']
        primary = native if native.battle_record_id is not None else (
            packet if packet.battle_record_id is not None else native)
        summary = primary.summary or native.summary or packet.summary
        if self._finished:
            errors = [f'{_LABELS[lane]}：{state.error or state.message}'
                      for lane, state in self._states.items() if state.phase == 'error']
            message = '；'.join(f'{_LABELS[lane]}：{state.message}'
                               for lane, state in self._states.items())
            if self._interrupted:
                message = '本次对照不完整。' + message
            discarded = self._discard_requested and all(
                state.persistence_status == 'discarded_restart' for state in self._states.values())
            self._state = replace(
                primary, phase='error' if errors else 'stopped', running=False, summary=summary,
                message=message, error='；'.join(errors) or None,
                error_code='CaptureComparisonError' if errors else None,
                persistence_status='discarded_restart' if discarded else primary.persistence_status,
                end_reason='scene_transition' if self._scene_transition and not errors else primary.end_reason,
            )
        else:
            phase = ('stopping' if self._stop_requested else
                     'running' if all(s.phase == 'running' for s in self._states.values()) else 'starting')
            message = {'starting': '正在启动 DLL 与抓包两路采集……',
                       'running': '双路采集中：DLL 与抓包分别保存，主视图优先显示 DLL。',
                       'stopping': '正在收尾两路采集，各路战报独立保存……'}[phase]
            self._state = BattleCaptureState(phase=phase, message=message, running=True, summary=summary)
        self.comparison_changed.emit(self.comparison)
        for handler in tuple(self._handlers):
            try:
                handler(self._state)
            except Exception:
                continue  # A view callback cannot prevent the other lane from being stopped.
