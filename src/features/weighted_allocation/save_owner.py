# 用独立账号代次和不可复活token拥有加权任务，提交与退场共享末端门闩。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event, RLock
from weakref import WeakSet

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from src.app.page_tasks import PageCommitLane


@dataclass(frozen=True, slots=True)
class WeightedOwnerIdentity:
    account_id: str
    generation: int
    user_database_path: Path

    @classmethod
    def from_dependencies(cls, dependencies):
        return cls(str(dependencies.account_id), int(dependencies.generation),
                   Path(dependencies.user_database_path).resolve())


@dataclass(frozen=True, slots=True, weakref_slot=True, eq=False)
class WeightedOperation:
    owner: WeightedSaveOwner
    identity: WeightedOwnerIdentity
    token: object = field(default_factory=object)
    cancelled: Event = field(default_factory=Event)
    preview: object | None = None

    def checkpoint(self):
        if not self.owner.accepts(self):
            raise CancelledError("加权任务已取消或账号上下文已失效")
        if self.preview is not None:
            source = getattr(self.preview, "ownership", None)
            if not isinstance(source, WeightedOperation) or source.owner is not self.owner:
                raise CancelledError("预览缺少原计算任务身份，请重新计算")
            source.checkpoint()

    @contextmanager
    def commit_context(self):
        # 仅末端验证至真实commit持锁；撤权先set Event，UI永不等待此锁。
        with self.owner._commit_fence:
            self.checkpoint()
            yield
        # 接受点之后Event改变不把已提交事务伪称回滚。


class WeightedSaveOwner(QObject):
    idle = Signal()

    def __init__(self, identity_provider, parent=None):
        super().__init__(parent)
        self._identity_provider = identity_provider
        self._commit_fence = RLock()
        self._operations = WeakSet()
        self._workers = set()
        self._lane = PageCommitLane(self)
        self._lane.settled.connect(self._lane_settled)
        self._task = None
        self._retiring = False
        self._closed = False
        self._management_holds = 0
        self._idle_callbacks = {}
        self._transition = None
        self._session_token = object()

    @property
    def current_task(self):
        return self._task

    def current_identity(self):
        return self._identity_provider()

    def projection_key(self):
        # 只读页面缓存代次：同账号撤销restore后也须重读，不混入库存generation。
        return self.current_identity(), self._session_token

    def can_submit(self):
        return not self._retiring and not self._closed and not self._management_holds

    def accepts(self, operation):
        return (isinstance(operation, WeightedOperation) and operation.owner is self
                and not operation.cancelled.is_set() and self.can_submit()
                and operation.identity == self.current_identity())

    def capture(self, dependencies, *, preview=None):
        identity = WeightedOwnerIdentity.from_dependencies(dependencies)
        if not self.can_submit() or identity != self.current_identity():
            raise CancelledError("加权任务正退场或账号已变化")
        handle = WeightedOperation(self, identity, preview=preview)
        self._operations.add(handle)
        return handle

    def begin_save(self, preview):
        source = getattr(preview, "ownership", None)
        if not isinstance(source, WeightedOperation) or source.owner is not self:
            raise CancelledError("预览缺少计算时冻结的账号身份，请重新计算")
        source.checkpoint()
        if self._lane.is_running() or self._task is not None:
            raise RuntimeError("加权方案正在保存，请等待完成")
        if (preview.account_id != source.identity.account_id
                or Path(preview.user_database_path).resolve() != source.identity.user_database_path):
            raise CancelledError("预览的账号或数据库路径不一致")
        return self.capture(source.identity, preview=preview)

    def submit(self, operation, target, committed, failed, *, application_failed):
        operation.checkpoint()
        if operation.preview is None or self._task is not None:
            raise RuntimeError("保存任务缺少原预览或已有提交")
        self._task = operation
        def deliver(callback, value):
            if self.current_task is operation and self.accepts(operation):
                callback(value)
        def run():
            operation.checkpoint()
            return target()
        accepted = self._lane.submit(run, lambda value: deliver(committed, value),
                                     lambda error: deliver(failed, error),
                                     lambda error: deliver(application_failed, error))
        if not accepted:
            self._task = None
            raise RuntimeError("加权保存提交通道忙碌")

    def track(self, worker):
        # 强引用一直保留到Qt finished；result_ready只是业务交付而非物理退场。
        self._workers.add(worker)
        worker.finished.connect(self._worker_finished)

    @Slot()
    def _worker_finished(self):
        self._workers.discard(self.sender())
        self._notify_idle()

    @Slot(bool)
    def _lane_settled(self, _success):
        self._task = None
        self._notify_idle()

    def try_retire(self):
        if self._workers or self._lane.is_running():
            return False
        if not self._commit_fence.acquire(blocking=False):
            return False
        self._commit_fence.release()
        return True

    def is_running(self):
        return not self.try_retire()

    def request_stop(self):
        self._retiring = True
        for handle in tuple(self._operations):
            handle.cancelled.set()
        return self.try_retire()

    def stop(self):
        # 直接AppContext调用的fail-closed兜底；正常UI先异步退场，索引尚未改变。
        if not self.request_stop():
            raise RuntimeError("加权任务尚未物理退场，请等待后重试账号切换")

    def rebuild(self):
        if not self.try_retire():
            raise RuntimeError("加权任务仍占有旧账号")
        if not self._closed:
            if self._retiring:
                self._session_token = object()
            self._retiring = False

    def close(self):
        self._closed = True
        return self.request_stop()

    @contextmanager
    def management_hold(self):
        if not self.request_stop():
            raise RuntimeError("账号管理需先等加权任务退场")
        self._management_holds += 1
        try:
            yield
        finally:
            self._management_holds -= 1
            self.rebuild()

    def when_idle(self, callback, *, timeout_ms=5000, timed_out=None):
        token = object()
        self._idle_callbacks[token] = callback
        def expire():
            if self._idle_callbacks.pop(token, None) is not None and timed_out is not None:
                timed_out()
        QTimer.singleShot(timeout_ms, expire)
        QTimer.singleShot(0, self._notify_idle)
        return lambda: self._idle_callbacks.pop(token, None)

    def defer_transition(self, key, continuation, timed_out):
        if self.request_stop():
            if self._transition is not None:
                self._transition[1]()
                self._transition = None
            return False
        if self._transition is not None:
            if self._transition[0] == key:
                return True  # 同一等待保留原截止时间，不通过重复close续期。
            self._transition[1]()
        identity = self.current_identity()
        def resume():
            self._transition = None
            if self.current_identity() == identity:
                continuation()
        def expired():
            self._transition = None
            timed_out()
        cancel = self.when_idle(resume, timed_out=expired)
        self._transition = key, cancel
        return True

    def _notify_idle(self):
        if not self.try_retire():
            return
        callbacks = tuple(self._idle_callbacks.values())
        self._idle_callbacks.clear()
        for callback in callbacks:
            callback()
        # 超时只撤销continuation；旧token永久失效，物理退场后可发起新任务。
        if self.try_retire() and not self._closed:
            self.rebuild()
            self.idle.emit()
