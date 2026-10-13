# 复用单次提交通道持有视觉入库及仅文件重试，物理终态后交付各自结果。
from __future__ import annotations

from functools import partial
from threading import Event
from typing import Callable

from PySide6.QtCore import QObject, Signal

from src.app.page_tasks import PageCommitLane
from src.features.scanning.dependencies import ScanningDependencies
from src.services.inventory.vision_commit import (
    VisionCommitRequest, VisionCommitResult, commit_vision_result, retry_vision_publication,
)
from src.services.inventory.visual_scan_commit import check_vision_result_resources


def _retry_files(retry, request, saved, token):
    # 纯 worker 输入：核对原解析资料与取消身份，不读取 QObject 或构造新保存请求。
    check_vision_result_resources(request.stats, cancel_check=token.is_set)
    return retry(request, saved, token)


class VisionCommitController(QObject):
    committed = Signal(object, object)
    failed = Signal(object, str)
    application_failed = Signal(object, str)
    publication_retried = Signal(object, object)
    publication_retry_failed = Signal(object, str)

    def __init__(self, current: Callable[[ScanningDependencies], bool], parent: QObject | None = None,
                 *, commit: Callable = commit_vision_result, retry: Callable = retry_vision_publication) -> None:
        super().__init__(parent)
        self._current = current  # 只在主线程交付使用，绝不传给 worker。
        self._commit = commit
        self._retry = retry
        self._lane = PageCommitLane(self)
        self._token = Event()
        self._closed = False
        self._request: VisionCommitRequest | None = None
        self._dependencies: ScanningDependencies | None = None
        self.last_result: VisionCommitResult | None = None

    def is_running(self) -> bool:
        return self._lane.is_running()

    def submit(self, dependencies: ScanningDependencies, request: VisionCommitRequest) -> bool:
        if self._closed or self.is_running() or not self._current(dependencies):
            return False
        self._token = Event()
        self.last_result = None
        self._request, self._dependencies = request, dependencies
        # commit 闭包只持有纯请求、Service 与线程安全 token，没有 QObject。
        return self._lane.submit(partial(self._commit, request, self._token),
            partial(self._deliver, dependencies), partial(self._fail, dependencies),
            partial(self._application_failed, dependencies))

    def _deliver(self, dependencies: ScanningDependencies, result: VisionCommitResult) -> None:
        if not self._closed and self._current(dependencies):
            self.last_result = result  # 同账号保留 DB/文件分离状态，不让旧代次填回新账号。
            self.committed.emit(dependencies, result)

    def _fail(self, dependencies: ScanningDependencies, error: str) -> None:
        if not self._closed and self._current(dependencies):
            self.failed.emit(dependencies, error)

    def _application_failed(self, dependencies: ScanningDependencies, error: str) -> None:
        if not self._closed and self._current(dependencies):
            self.application_failed.emit(dependencies, error)

    def may_continue(self, dependencies: ScanningDependencies, result: VisionCommitResult) -> bool:
        return (not self._closed and not self._token.is_set() and self._current(dependencies)
                and self.last_result is result and not self.is_running())

    def can_retry_publication(self) -> bool:
        saved, request, deps = self.last_result, self._request, self._dependencies
        capture_token = request.stats.get("capture_cancel_token") if request is not None else None
        return bool(deps is not None and request is not None and saved is not None
            and self.may_continue(deps, saved) and not saved.post_commit_error
            and saved.snapshot_id is not None and saved.plan is not None
            and saved.publication is not None and saved.publication.status == "pending"
            and saved.plan.stage == request.stats.get("capture_stage")
            and not (capture_token is not None and capture_token.is_set()))

    def retry_publication(self) -> bool:
        """复用确切 saved/request 和同一提交 lane；从不回到 submit 或创建快照。"""
        if not self.can_retry_publication():
            return False
        deps, request, saved = self._dependencies, self._request, self.last_result
        return self._lane.submit(partial(_retry_files, self._retry, request, saved, self._token),
            partial(self._deliver_retry, deps), partial(self._fail_retry, deps),
            partial(self._fail_retry, deps))

    def _deliver_retry(self, dependencies: ScanningDependencies, result: VisionCommitResult) -> None:
        if not self._closed and self._current(dependencies):
            self.last_result = result
            # 独立文件交付信号，不重复保存后的归档、弹窗或分配链。
            self.publication_retried.emit(dependencies, result)

    def _fail_retry(self, dependencies: ScanningDependencies, error: str) -> None:
        if not self._closed and self._current(dependencies):
            self.publication_retry_failed.emit(dependencies, error)

    def cancel(self) -> None:
        self._token.set()  # 只撤销副作用，不假装物理线程已经结束。

    def reset_account_state(self) -> None:
        self.cancel()
        self.last_result = self._request = self._dependencies = None

    def close(self) -> None:
        self._closed = True
        self.cancel()
