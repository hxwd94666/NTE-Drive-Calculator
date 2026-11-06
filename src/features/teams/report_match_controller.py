# 单通道持有匹配线程和取消令牌，末次请求回填，关闭等待本 owner 物理退场。
from concurrent.futures import CancelledError
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal


class _MatchWorker(QThread):
    published = Signal(object)
    delivered = Signal(object)
    failed = Signal(str)

    def __init__(self, task, token, parent):
        super().__init__(parent)
        self.task, self.token = task, token

    def run(self):
        try:
            value = self.task(self.token.is_set, self.published.emit)
            if not self.token.is_set():
                self.delivered.emit(value)
        except CancelledError:
            pass
        except Exception as error:
            if not self.token.is_set():
                self.failed.emit(str(error))


class ReportMatchController(QObject):
    result = Signal(object)
    progress = Signal(object)
    error = Signal(str)
    drained = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._worker = None
        self._token = Event()
        self._pending = None
        self._sequence = 0
        self._closed = False

    def is_running(self):
        return self._worker is not None

    def submit(self, task):
        if self._closed:
            return
        self.cancel()
        self._pending = task
        if self._worker is None:
            self._start()

    def _start(self):
        task, self._pending = self._pending, None
        if task is None or self._closed:
            return
        self._token = Event()
        token, sequence = self._token, self._sequence
        worker = _MatchWorker(task, token, self)
        self._worker = worker

        def current():
            return not self._closed and not token.is_set() and sequence == self._sequence

        worker.published.connect(lambda value: self.progress.emit(value) if current() else None)
        worker.delivered.connect(lambda value: self.result.emit(value) if current() else None)
        worker.failed.connect(lambda value: self.error.emit(value) if current() else None)
        worker.finished.connect(self._finished)
        worker.start()

    def _finished(self):
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        if self._pending is not None and not self._closed:
            self._start()
        else:
            self.drained.emit()

    def cancel(self):
        self._sequence += 1
        self._token.set()
        self._pending = None

    def close(self):
        self._closed = True
        self.cancel()
