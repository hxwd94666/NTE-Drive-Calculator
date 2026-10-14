# 持有鉴定两阶段任务、取消代次及物理空闲后的临时图片清理。
from __future__ import annotations

from concurrent.futures import CancelledError
from dataclasses import dataclass, field, replace
from functools import partial
from pathlib import Path
from threading import Event
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

from src.app.page_tasks import PageRequest, PageTaskLane
from src.features.identification.dependencies import IdentificationDependencies
from src.integrations.vision.recognition.identification_temp_files import (
    cleanup_identify_clipboard_files, is_identify_clipboard_file,
)
from src.integrations.vision.recognition.resources import RecognitionResources
from src.services.recognition.resources import load_recognition_resources
from src.services.identification.tasks import (
    IdentificationImageRequest, IdentificationMatchRequest,
    parse_identification_images, match_identification_items,
)


@dataclass(frozen=True)
class IdentificationTask:
    sequence: int
    dependencies: IdentificationDependencies
    cancel: Event = field(default_factory=Event, compare=False, repr=False)


class IdentificationTaskController(QObject):
    parsed = Signal(object, object)
    matched = Signal(object, object)
    failed = Signal(object, str)
    cleaned = Signal(object, object)
    idle = Signal()

    def __init__(self, dependencies: Callable[[], IdentificationDependencies],
                 parent: QObject | None = None, *,
                 image_reader: Callable[..., Any] = parse_identification_images,
                 match_reader: Callable[..., Any] = match_identification_items) -> None:
        super().__init__(parent)
        self._dependencies = dependencies  # 仅主线程交付核对时调用，不交给 read。
        self._image_reader = image_reader
        self._match_reader = match_reader
        self._lane = PageTaskLane(self)
        self._lane.idle.connect(self._on_idle)
        self._sequence = 0
        # 图片由 worker 返回资料；手工在解析前冻结。两条入口匹配时都不重绑最新规则。
        self._resources: RecognitionResources | None = None
        self._task: IdentificationTask | None = None
        self._phase = False
        self.closed = False
        self._cleanup: list[tuple[IdentificationTask, tuple[Path, ...]]] = []

    def begin(self, dependencies: IdentificationDependencies) -> IdentificationTask | None:
        if self.closed:
            return None
        self.cancel()
        self._sequence += 1
        self._task = IdentificationTask(self._sequence, dependencies)
        self._phase = True
        return self._task

    def is_current(self, task: IdentificationTask | None) -> bool:
        return (not self.closed and task is not None and self._task is task
                and not task.cancel.is_set()
                and self._dependencies() == task.dependencies)

    def resources_for(self, task: IdentificationTask | None) -> RecognitionResources | None:
        """确认页面只读本次解析资料，不在模态弹窗期间重新读取配置。"""
        return self._resources if self.is_current(task) else None

    def freeze_manual_resources(self, task: IdentificationTask) -> RecognitionResources:
        """手工/文本剪贴板共用本次词条与静态身份，不初始化图片模板或 OCR。"""
        if not self.is_current(task):
            raise CancelledError("装备鉴定已取消")
        if self._resources is None:
            dependencies = task.dependencies
            resources = load_recognition_resources(dependencies.config_dir, dependencies.static_database_path)
            # 读取失败不留下半份资料；读取期间账号失效也不把候选挂到另一任务。
            if not self.is_current(task):
                raise CancelledError("装备鉴定已取消")
            self._resources = resources
        return self._resources

    def has_worker(self) -> bool:
        return self._lane.is_running()

    def is_running(self) -> bool:
        # 确认弹窗仍属于同一操作；交付完成也须等 QThread 物理结束。
        return self._phase or self.has_worker()

    def submit_images(self, task: IdentificationTask,
                      request: IdentificationImageRequest) -> None:
        if not self.is_current(task):
            return
        owned = tuple(job.path for job in request.jobs
                      if is_identify_clipboard_file(job.path, task.dependencies.account_data_root))
        if owned:
            self._cleanup.append((task, owned))
        self._submit(task, "parse", partial(self._image_reader, request,
                                           cancel_check=task.cancel.is_set))

    def submit_matches(self, task: IdentificationTask,
                       request: IdentificationMatchRequest) -> None:
        if self.is_current(task):
            if self._resources is not None:
                request = replace(request, resources=self._resources)
            self._submit(task, "match", partial(self._match_reader, request,
                                               cancel_check=task.cancel.is_set))

    def _submit(self, task: IdentificationTask, stage: str, read: Callable[[], Any]) -> None:
        self._phase = True
        # read 只有纯函数、冻结请求和线程安全 Event，未捕获 QObject。
        self._lane.submit(PageRequest((task.sequence, stage), read,
                                      partial(self._deliver, task, stage),
                                      partial(self._fail, task)))

    def _deliver(self, task: IdentificationTask, stage: str, value: Any) -> None:
        if not self.is_current(task):
            if self._task is task:
                self.cancel()
            return
        if stage == "parse":
            self._resources = value.resources
            self.parsed.emit(task, list(value.items))
        else:
            self.finish(task)
            self.matched.emit(task, value)

    def _fail(self, task: IdentificationTask, error: str) -> None:
        if self.is_current(task):
            self.finish(task)
            self.failed.emit(task, error)
        elif self._task is task:
            self.cancel()

    def finish(self, task: IdentificationTask) -> None:
        if self._task is task:
            self._phase = False

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel.set()
        self._task = None
        self._resources = None
        self._phase = False
        self._lane.cancel()
        if not self.has_worker():
            self._on_idle()

    def close(self) -> None:
        self.closed = True
        self.cancel()
        self._lane.close()

    @Slot()
    def _on_idle(self) -> None:
        if self.has_worker():
            return
        if self._task is not None and not self.is_current(self._task):
            self._task.cancel.set()
            self._task = None
            self._resources = None
            self._phase = False
        cleanup, self._cleanup = self._cleanup, []
        # discarded/cancel 不是物理结束；旧任务和 latest 都不读图片后才清理。
        for task, paths in cleanup:
            cleanup_identify_clipboard_files(list(paths), task.dependencies.account_data_root)
            self.cleaned.emit(task, paths)
        if not self._phase:
            self.idle.emit()
