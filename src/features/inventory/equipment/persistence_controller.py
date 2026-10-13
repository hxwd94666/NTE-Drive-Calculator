# 独占配装管理后台任务及账号撤权，提交物理终态和页面投影分别确认。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import partial
from threading import Event
from weakref import WeakSet

from PySide6.QtCore import QObject, QTimer, Signal

from src.app.page_tasks import PageCommitLane
from src.domain.equipment.loadout_management import LoadoutAccount


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class LoadoutOperation:
    account: LoadoutAccount
    cancelled: Event = field(default_factory=Event)
    submitted: Event = field(default_factory=Event)

    def checkpoint(self):
        if self.cancelled.is_set():
            raise CancelledError("配装管理已取消或账号上下文已失效")

    @contextmanager
    def commit_context(self):
        # 此检查为提交接受点。之后撤权只阻止新操作/交付，owner等待真实线程终态。
        self.checkpoint()
        yield


class EquipmentPersistenceController(QObject):
    idle = Signal()
    retiring = Signal()
    busy_changed = Signal(bool)

    def __init__(self, identity_provider, service, parent=None):
        super().__init__(parent)
        self._identity_provider = identity_provider
        self._service = service
        self._lane = PageCommitLane(self)
        self._lane.settled.connect(self._settled)
        self._operations = WeakSet()
        self._retiring = False
        self._closed = False
        self._holds = 0
        self._delivering = 0
        self._transition = None
        self._transition_key = None
        self._transition_timer = QTimer(self)
        self._transition_timer.setSingleShot(True)
        self._transition_timer.timeout.connect(self._transition_timeout)
        self.last_committed_result = None

    def accepts(self, operation):
        return (not self._closed and not self._retiring and not self._holds
                and operation in self._operations and not operation.cancelled.is_set()
                and operation.account == self._identity_provider())

    def is_running(self):
        return self._lane.is_running() or bool(self._delivering)

    def prepare(self, prepared, failed, *, slot_id=None, plan_id=None):
        if self._lane.is_running() or self._closed or self._retiring or self._holds:
            failed("配装管理正在处理，请等待完成后重试。")
            return False
        operation = LoadoutOperation(self._identity_provider())
        self._operations.add(operation)
        return self._run(operation, partial(self._service.prepare, operation,
                         slot_id=slot_id, plan_id=plan_id),
                         lambda snapshot: prepared(snapshot, operation), failed, failed)

    def commit(self, operation, mutation, committed, failed, application_failed):
        if not self.accepts(operation):
            return False
        if operation.submitted.is_set():
            failed("本次配装请求已提交，请刷新页面后再操作。")
            return False
        if self._lane.is_running():
            failed("配装管理正在处理，请等待完成后重试。")
            return False
        operation.submitted.set()
        return self._run(operation, partial(self._service.mutate, operation, mutation),
                         committed, failed, application_failed, writing=True)

    def _run(self, operation, target, committed, failed, application_failed, *, writing=False):
        if self._lane.is_running():
            failed("配装管理正在处理，请等待完成后重试。")
            return False

        def deliver(callback, value):
            if not self.accepts(operation):
                return
            self._delivering += 1
            try:
                callback(value)
            finally:
                self._delivering -= 1

        def complete(value):
            if writing:
                # 物理提交事实独立于账号是否已请求退场，也不因UI投影异常被清除。
                self.last_committed_result = value
            deliver(committed, value)

        accepted = self._lane.submit(target, complete,
                                     partial(deliver, failed),
                                     partial(deliver, application_failed))
        if accepted:
            self.busy_changed.emit(True)
        return accepted

    def _settled(self, _success):
        if self.is_running():
            return
        self.busy_changed.emit(False)
        if self._transition is not None:
            identity, continuation, _timed_out = self._transition
            self._transition = None
            self._transition_timer.stop()
            if identity == self._identity_provider():
                continuation()
        if not self.is_running() and not self._closed:
            self.rebuild()
            self.idle.emit()

    def request_stop(self):
        self._retiring = True
        for operation in tuple(self._operations):
            operation.cancelled.set()
        self.retiring.emit()
        return not self.is_running()

    def stop(self):
        if not self.request_stop():
            raise RuntimeError("配装管理任务尚未退场，请等待后重试账号切换")

    def rebuild(self):
        if self.is_running():
            raise RuntimeError("配装管理任务仍占有旧账号")
        if not self._closed:
            self._retiring = False

    def close(self):
        self._closed = True
        self._transition = None
        self._transition_timer.stop()
        return self.request_stop()

    @contextmanager
    def management_hold(self):
        if not self.request_stop():
            raise RuntimeError("账号管理需先等待配装任务退场")
        self._holds += 1
        try:
            yield
        finally:
            self._holds -= 1
            self.rebuild()

    def defer_transition(self, key, continuation, timed_out, *, timeout_ms=5000):
        if self.request_stop():
            self._transition = None
            self._transition_timer.stop()
            self.rebuild()
            return False
        identity = self._identity_provider()
        if self._transition is not None and self._transition_key == key:
            return True
        self._transition_key = key
        self._transition = identity, continuation, timed_out
        self._transition_timer.start(timeout_ms)
        return True

    def cancel_transition(self):
        """新账号选择（含选回当前账号）撤销旧continuation，不复活旧操作token。"""
        self._transition = None
        self._transition_key = None
        self._transition_timer.stop()
        if not self.is_running() and not self._closed:
            self.rebuild()

    def _transition_timeout(self):
        transition, self._transition = self._transition, None
        if transition is not None:
            transition[2]()


def wait_for_equipment_persistence(window, key, continuation):
    from PySide6.QtWidgets import QMessageBox
    return window.equipment_persistence_controller.defer_transition(
        key, continuation,
        lambda: QMessageBox.information(window, "正在等待配装任务退场",
            "本次等待已结束，账号与窗口仍保留。任务退场后可重试。"),
    )
