# 在后台保存配装时显示持续响应的模态进度，保持原有同步保存返回值。
from __future__ import annotations

from concurrent.futures import CancelledError

from PySide6.QtCore import QEventLoop, Qt, QTimer, Slot
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.theme import current_style_sheet
from src.app.workers import WorkerThread


class AllocationSaveProgress(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("保存配装")
        self.setStyleSheet(current_style_sheet())
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, False)
        layout = QVBoxLayout(self)
        self.label = QLabel("正在准备保存方案…", self)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)
        self._worker = None
        self._result = None
        self._error = None
        self._finished = False
        self._wait_loop = QEventLoop(self)
        self.resize(380, 110)
        fit_dialog_to_available_screen(self)

    @Slot(object)
    def set_stage(self, message):
        text, completed, total = message
        self.label.setText(text)
        self.progress.setRange(0, total)
        self.progress.setValue(completed)
        # 下一轮页面渲染前刷新本窗口进度；进度信号不是事务提交或任务物理退场证据。
        self.label.repaint()
        self.progress.repaint()

    @Slot(object)
    def _receive(self, result):
        self._result = result

    @Slot(str)
    def _failed(self, error):
        self._error = error

    @Slot()
    def _complete(self):
        self._finished = True
        self._wait_loop.quit()

    def reject(self):
        # 已进入保存的本地写入必须先结束；窗口关闭不替代任务取消/写入终态。
        if self._finished:
            super().reject()

    def closeEvent(self, event):
        if not self._finished:
            event.ignore()
        else:
            super().closeEvent(event)

    def run(self, target, owner):
        worker = WorkerThread(target=lambda: target(worker.progress.emit), parent=self)
        self._worker = worker
        owner._save_worker = worker
        worker.progress.connect(self.set_stage)
        worker.result_ready.connect(self._receive)
        worker.error.connect(self._failed)
        worker.finished.connect(self._complete)
        QTimer.singleShot(0, worker.start)
        try:
            # 持久化与展示刷新期间保持同一窗口；事件循环让 UI 响应，wait 再确认线程物理退场。
            self.show()
            self._wait_loop.exec()
            worker.wait()
            if self._error is not None:
                if self._error == "任务已取消":
                    raise CancelledError(self._error)
                raise RuntimeError(self._error)
            return self._result
        finally:
            owner._save_worker = None
            worker.deleteLater()
