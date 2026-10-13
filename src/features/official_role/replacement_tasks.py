# 复用页面读取与提交通道拥有单次替换，撤权只丢投递而不伪造已提交回滚。
from __future__ import annotations

from concurrent.futures import CancelledError
from contextlib import contextmanager
from threading import Event, RLock

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtWidgets import QDialog, QProgressDialog
from shiboken6 import isValid

from src.app.page_tasks import PageCommitLane, PageRequest, PageTaskLane
from src.features.official_role.dependencies import OfficialRoleDependencies


class ReplacementTaskOwner(QObject):
    """只拥有这次选择；既有 lanes 负责物理线程，输入和取消 gate 不含控件。"""

    def __init__(self, parent, *, app_context=None, is_current=lambda: True):
        super().__init__(parent)
        self.dependencies = (OfficialRoleDependencies.from_app_context(app_context)
                             if app_context is not None else None)
        self._app_context = app_context
        self._is_current = is_current
        self._cancelled = Event()
        self._commit_fence = RLock()
        self._reads = PageTaskLane(self)
        self._writes = PageCommitLane(self)
        self._reads.idle.connect(self._release_if_closed)
        self._writes.settled.connect(self._release_if_closed)
        self._closed = False
        self._unregister = (app_context.register_account_lifecycle(self)
                            if app_context is not None else lambda: None)

    def checkpoint(self):
        # 该函数在worker调用：仅查看不可复活Event，不读取Qt或最新账号配置。
        if self._cancelled.is_set():
            raise CancelledError("替换任务已取消或账号上下文已失效")

    @contextmanager
    def commit_context(self):
        # 沿用既有提交接受点：UI撤权先set Event，接受后不再复判为回滚。
        with self._commit_fence:
            self.checkpoint()
            yield

    def current(self):
        if self._cancelled.is_set() or self._closed or not isValid(self.parent()):
            return False
        if (self.dependencies is not None
                and self.dependencies != OfficialRoleDependencies.from_app_context(self._app_context)):
            return False
        return self.parent().isVisible() and self._is_current()

    def cancel(self):
        self._cancelled.set()
        self._reads.cancel()

    def is_running(self):
        if self._reads.is_running() or self._writes.is_running():
            return True
        acquired = self._commit_fence.acquire(blocking=False)
        if acquired:
            self._commit_fence.release()
        return not acquired

    def stop(self):
        self.cancel()
        if self.is_running():
            # AppContext.stop在变更generation之前调用；旧线程未末态则保留旧账号。
            raise RuntimeError("替换任务尚未物理结束，请稍候再切换账号")

    def rebuild(self, _account):
        self.cancel()

    def start(self):
        pass  # 单次选择在账号切换后永不自动复活或重试。

    def close(self):
        self._closed = True
        self.cancel()
        self._reads.close()
        self._release_if_closed()

    def _release_if_closed(self, *_args):
        if self._closed and not self.is_running():
            self._unregister()
            self._unregister = lambda: None
            self.deleteLater()

    def prepare(self, read):
        """保留模态bool交互，但exec只泵Qt事件，SHA/query在已有PageTaskLane。"""
        result, errors = [], []
        progress = QProgressDialog("正在准备替换候选…", "取消", 0, 0, self.parent())
        progress.setWindowTitle("替换优化")
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.canceled.connect(self.cancel)
        monitor = QTimer(progress)
        monitor.setInterval(25)
        def watch():
            if not self.current():
                self.cancel()
                progress.reject()
        monitor.timeout.connect(watch)
        def work():
            self.checkpoint()
            value = read()
            self.checkpoint()
            return value
        def deliver(value):
            if self.current():
                result.append(value)
                progress.accept()
            else:
                self.cancel()
                progress.reject()
        def failed(error):
            if self.current():
                errors.append(error)
            progress.reject()
        # 每次选择有不可复用key，避免同role/UID新callback消费旧active结果。
        key = object()
        QTimer.singleShot(0, lambda: self._reads.submit(PageRequest(key, work, deliver, failed)))
        monitor.start()
        accepted = progress.exec() == QDialog.Accepted
        monitor.stop()
        progress.deleteLater()
        if errors:
            raise RuntimeError(errors[0])
        if not accepted:
            self.cancel()
        return result[0] if accepted and result else None

    def submit(self, commit, committed, failed, application_failed):
        self.checkpoint()
        def work():
            self.checkpoint()
            return commit()
        return self._writes.submit(work, committed, failed, application_failed)
