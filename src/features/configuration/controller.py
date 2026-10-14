# 维护基础权重页读写任务、缓存与迟到结果；业务规则和事务归独立 Service。
from __future__ import annotations

from collections.abc import Callable, Iterable
from copy import deepcopy
from typing import Any, TypeVar

from PySide6.QtCore import QObject, Qt, Signal

from src.app.page_tasks import PageCommitLane, PageRequest, PageTaskLane
from src.domain.basic_weight import BasicWeightDependencies
from src.observability import OperationContext
from src.services.characters.basic_weight.service import BasicWeightService


_T = TypeVar("_T")


class BasicWeightController(QObject):
    """拥有有界后台 lane；不实现 SQL、目录规则或权重公式。"""

    changed = Signal()

    def __init__(self, dependencies: BasicWeightDependencies, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.dependencies = dependencies
        self.service = BasicWeightService(dependencies)
        self._reads = PageTaskLane(self)
        self._writes = PageCommitLane(self)
        self._epoch = 0
        self._closed = False
        self._retiring = False
        self._form_cache: dict[str, Any] | None = None
        self._form_cache_key: tuple[tuple, ...] | None = None

    def is_writing(self) -> bool:
        return self._writes.is_running()

    def cancel_reads(self) -> None:
        self._reads.cancel()

    def request_form_data(self, apply: Callable[[dict[str, Any]], None], failed: Callable[[str], None]) -> None:
        if not self._closed and not self.is_writing():
            self._reads.submit(PageRequest(
                (self.dependencies, self._epoch), lambda: deepcopy(self._cached_form()), apply, failed,
            ))

    def _cached_form(self) -> dict[str, Any]:
        # 指纹只用于失效检测，不冒充数据库事务；输入变化最多重读一次，不投影已知混合结果。
        for _attempt in range(2):
            key = self.service.source_key()
            if self._form_cache is None or key != self._form_cache_key:
                candidate = self.load_form_data()
            else:
                candidate = self._form_cache
            if key == self.service.source_key():
                self._form_cache, self._form_cache_key = candidate, key
                return candidate
        self._form_cache = self._form_cache_key = None
        raise RuntimeError("角色配置在加载期间变化，请重新载入")

    def submit_change(
        self, work: Callable[[], _T], committed: Callable[[_T], None],
        failed: Callable[[str], None], application_failed: Callable[[str], None],
    ) -> bool:
        if self._closed or self.is_writing():
            return False
        self._epoch += 1
        self._reads.cancel()

        def done(value: _T) -> None:
            self._form_cache_key = None
            self.changed.emit()
            committed(value)

        return self._writes.submit(work, done, failed, application_failed)

    def when_commit_settled(self, callback: Callable[[bool], None]) -> None:
        self._writes.settled.connect(callback, Qt.ConnectionType.SingleShotConnection)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # 关闭使读取失效，但已接受写入仍保留终态确认；不在主线程等待 worker。
        self._reads.close()
        if self._reads.is_running():
            self._reads.idle.connect(self._release_cache, Qt.ConnectionType.SingleShotConnection)
        else:
            self._release_cache()

    def _release_cache(self) -> None:
        self._form_cache = self._form_cache_key = None

    def retire(self) -> None:
        """换代时停止接受工作，等物理读写终态后回收整个 owner。"""
        if self._retiring:
            return
        self._retiring = True
        self.close()
        # 读取失效不等于线程结束；已接受写入也必须先保留终态确认。
        # 两条 lane 可能同时运行，任何一条先结束都只重新检查，不提前销毁 QThread。
        self._reads.idle.connect(self._delete_when_idle, Qt.ConnectionType.SingleShotConnection)
        self._writes.settled.connect(self._delete_when_idle, Qt.ConnectionType.SingleShotConnection)
        self._delete_when_idle()

    def _delete_when_idle(self, _success: bool = True) -> None:
        if not self._reads.is_running() and not self.is_writing():
            self.deleteLater()

    def operation(self) -> OperationContext:
        return self.service.operation()

    def load_form_data(self) -> dict[str, Any]:
        return self.service.load_form_data()

    def save_changes(
        self, data: dict, dirty_character_ids: set[int], dirty_shape_bonus_ids: set[int],
        dirty_board_ids: set[int] | None = None, dirty_target_suit_ids: set[int] | None = None,
    ) -> None:
        self.service.save_changes(data, dirty_character_ids, dirty_shape_bonus_ids, dirty_board_ids, dirty_target_suit_ids)

    def create_custom_role(self, name_zh: str) -> dict:
        return self.service.create_custom_role(name_zh)

    def delete_custom_role(self, character_id: int) -> None:
        self.service.delete_custom_role(character_id)

    def reset_weights(self, character_ids: Iterable[int]) -> dict[int, dict]:
        return self.service.reset_weights(character_ids)

    def log_dirty_exit(self, action: str, dirty_count: int) -> None:
        self.service.log_dirty_exit(action, dirty_count)
