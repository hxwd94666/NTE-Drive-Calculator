# 绑定单个养成草稿的自动历史保存状态及针对原信封的重试入口。
from __future__ import annotations

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from src.domain.cultivation_history import HistoryCalculationEnvelope, HistoryContextExpired, HistoryPayload
from src.features.toolbox.cultivation.history_controller import CultivationHistoryController, HistoryOperationResult
from src.services.progression.history_service import CultivationHistoryService


class CultivationHistoryDraftBinding(QObject):
    status_changed = Signal(str, bool)
    saved = Signal(object)

    def __init__(
        self, service: CultivationHistoryService, controller: CultivationHistoryController,
        *, mode: str, parent: QObject,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._controller = controller
        self._mode = mode
        self._session = service.new_session(mode)
        self._request_id = 0
        self._retry: tuple[HistoryCalculationEnvelope, Callable[[], HistoryPayload]] | None = None
        self._closed = False
        controller.completed.connect(self._completed)

    def reset(self) -> None:
        self._service.discard_session(self._session)
        self._session = self._service.new_session(self._mode)
        self._request_id = 0
        self._retry = None
        self.status_changed.emit("", False)

    def invalidate(self) -> None:
        self._service.invalidate(self._session)
        self._retry = None
        self._request_id = 0
        self.status_changed.emit("", False)

    def freeze(self, configuration: object, *, explicit: bool) -> HistoryCalculationEnvelope | None:
        self._retry = None
        self.status_changed.emit("", False)
        try:
            return self._service.freeze(self._session, configuration, explicit_calculation=explicit)
        except (ValueError, HistoryContextExpired) as error:
            self.status_changed.emit(f"本次历史输入未准备完成：{error}", False)
            return None

    def accept(
        self, envelope: HistoryCalculationEnvelope | None, project: Callable[[], HistoryPayload],
        *, history_error: str | None = None,
    ) -> None:
        if envelope is None or self._closed:
            return
        if history_error is not None:
            self.status_changed.emit(f"本次计算已完成，历史保存失败：{history_error}", False)
            return
        self._retry = (envelope, project)
        self.retry()

    def retry(self) -> None:
        if self._retry is None or self._closed:
            return
        envelope, project = self._retry
        try:
            self._service.assert_current(envelope)
        except HistoryContextExpired:
            self._retry = None
            self.status_changed.emit("旧历史保存请求已撤销；请使用当前草稿重新计算。", False)
            return

        self.status_changed.emit("本次计算已完成，正在保存历史。", False)
        self._request_id = self._controller.submit("save", partial(self._service.save_projected, envelope, project))

    def _completed(self, outcome: object) -> None:
        if (isinstance(outcome, HistoryOperationResult) and not self._closed
                and outcome.operation == "delete" and outcome.error_code is None):
            try:
                suppressed = self._service.saving_suppressed(self._session)
            except HistoryContextExpired:
                suppressed = True
            if suppressed:
                self._retry = None
                self._request_id = 0
                self.status_changed.emit("对应历史已删除；下一次主动计算会建立新记录。", False)
            return
        if (not isinstance(outcome, HistoryOperationResult) or self._closed
                or outcome.operation != "save" or outcome.request_id != self._request_id):
            return
        if outcome.error_code is None:
            self._retry = None
            self.status_changed.emit("历史已保存；本草稿重新计算会更新同一条记录。", False)
            self.saved.emit(outcome.value)
        elif outcome.error_code == "expired":
            self._retry = None
            self.status_changed.emit("旧历史保存请求已撤销；当前计算结果保持不变。", False)
        else:
            can_retry = self._retry is not None
            if can_retry:
                try:
                    self._service.assert_current(self._retry[0])
                except HistoryContextExpired:
                    self._retry = None
                    can_retry = False
            self.status_changed.emit(f"本次计算已完成，历史保存失败：{outcome.message}", can_retry)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._retry = None
        self._service.discard_session(self._session)
        self._controller.completed.disconnect(self._completed)
        self.deleteLater()


class CultivationHistorySaveStatus(QWidget):
    def __init__(self, binding: CultivationHistoryDraftBinding, parent: QWidget) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._label = QLabel(self)
        self._label.setWordWrap(True)
        self._retry = QPushButton("重试保存历史", self)
        self._retry.clicked.connect(binding.retry)
        layout.addWidget(self._label, 1)
        layout.addWidget(self._retry)
        binding.status_changed.connect(self._set_status)
        self.hide()

    def _set_status(self, message: str, can_retry: bool) -> None:
        from PySide6.QtCore import Qt

        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setText(message)
        self._retry.setVisible(can_retry)
        self.setVisible(bool(message))
