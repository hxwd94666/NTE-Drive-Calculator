# 在 Tab 拖拽结束时持久化其项目顺序。
"""拖拽结束后，通过注入的正式身份与保存回调持久化顺序。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QTabWidget
from shiboken6 import isValid


class PersistentTabOrderBinding(QObject):
    """仅观察本 TabBar；拖拽中不保存，顺序未变不重复提交。"""

    def __init__(
        self,
        tabs: QTabWidget,
        *,
        item_id_at: Callable[[int], Any],
        save_order: Callable[[Sequence[Any]], None],
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        super().__init__(tabs)
        self._tabs = tabs
        self._tab_bar = tabs.tabBar()
        self._item_id_at = item_id_at
        self._save_order = save_order
        self._on_error = on_error
        self._drag_active = False
        self._drag_changed = False
        self._last_saved_order = self.current_order()
        tabs.setMovable(True)
        self._tab_bar.installEventFilter(self)
        self._tab_bar.tabMoved.connect(self._on_tab_moved)

    def current_order(self) -> tuple[Any, ...]:
        if not isValid(self._tabs):
            return ()
        return tuple(self._item_id_at(index) for index in range(self._tabs.count()))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._tab_bar:
            if (
                event.type() == QEvent.MouseButtonPress
                and event.button() == Qt.LeftButton
            ):
                self._drag_active = True
                self._drag_changed = False
            elif (
                event.type() == QEvent.MouseButtonRelease
                and event.button() == Qt.LeftButton
            ):
                should_save = self._drag_active and self._drag_changed
                self._drag_active = False
                self._drag_changed = False
                if should_save:
                    QTimer.singleShot(0, self.persist_current_order)
        return False

    def _on_tab_moved(self, _from_index: int, _to_index: int) -> None:
        if self._drag_active:
            self._drag_changed = True

    def persist_current_order(self) -> None:
        if not isValid(self._tabs) or not isValid(self._tab_bar):
            return
        order = self.current_order()
        if order == self._last_saved_order:
            return
        try:
            self._save_order(order)
        except Exception as exc:
            if self._on_error is not None:
                self._on_error(exc)
            return
        self._last_saved_order = order


def bind_persistent_tab_order(
    tabs: QTabWidget,
    *,
    item_id_at: Callable[[int], Any],
    save_order: Callable[[Sequence[Any]], None],
    on_error: Callable[[Exception], None] | None = None,
) -> PersistentTabOrderBinding:
    """绑定拖拽完成事件，保存失败保留上次成功顺序并交给错误回调。"""

    return PersistentTabOrderBinding(
        tabs,
        item_id_at=item_id_at,
        save_order=save_order,
        on_error=on_error,
    )
