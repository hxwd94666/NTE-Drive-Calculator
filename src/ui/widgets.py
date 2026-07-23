# 提供可搜索下拉框和禁滚轮输入控件。
"""Reusable PySide widgets shared by several pages."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QComboBox, QCompleter, QDoubleSpinBox, QSpinBox


def match_pinyin(name: str, filt: str) -> bool:
    """Case-insensitive Chinese/name/pinyin matcher used by searchable controls."""

    if not filt:
        return True
    keyword = filt.lower()
    text = str(name or "").lower()
    if keyword in text:
        return True
    try:
        from pypinyin import Style, lazy_pinyin

        parts = lazy_pinyin(str(name), style=Style.NORMAL)
        if keyword in "".join(parts).lower():
            return True
        return keyword in "".join(part[0] for part in parts if part).lower()
    except ImportError:
        return False


class NoWheelComboBox(QComboBox):
    """Combo box that ignores mouse-wheel changes."""

    def wheelEvent(self, event):
        event.ignore()


class SearchableComboBox(NoWheelComboBox):
    """Editable combo box with popup completion and pinyin search."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._search_items = []
        self._search_updating = False
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)

        self._completion_model = QStringListModel(self)
        self._completer = QCompleter(self._completion_model, self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchContains)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)
        self._completer.activated[str].connect(self._on_completion_activated)
        popup = self._completer.popup()
        if popup:
            popup.setFocusPolicy(Qt.NoFocus)

        if self.lineEdit():
            self.lineEdit().setClearButtonEnabled(True)
            self.lineEdit().installEventFilter(self)
            self.lineEdit().textEdited.connect(self._filter_items)
            self.lineEdit().editingFinished.connect(self._commit_typed_text)
            self.lineEdit().setCompleter(self._completer)
        self.activated.connect(self._commit_current_item)

    def refresh_search_items(self):
        self._search_items = [(self.itemText(i), self.itemData(i)) for i in range(self.count())]
        self._set_completion_items([label for label, _ in self._search_items])

    def _set_completion_items(self, labels):
        self._completion_model.setStringList(list(labels))

    def _filter_items(self, text):
        if self._search_updating:
            return
        if not self._search_items:
            self.refresh_search_items()
        text = text.strip()
        filtered = [item for item in self._search_items if match_pinyin(item[0], text)]
        self._set_completion_items([label for label, _ in (filtered or self._search_items)])
        if self.lineEdit():
            self.lineEdit().setFocus(Qt.OtherFocusReason)
            self.lineEdit().setCursorPosition(len(text))
            self._completer.setCompletionPrefix("")
            self._completer.complete()

    def _open_all(self):
        if not self._search_items:
            self.refresh_search_items()
        self._set_completion_items([label for label, _ in self._search_items])
        if self.lineEdit():
            self.lineEdit().setFocus(Qt.OtherFocusReason)
            self.lineEdit().selectAll()
            self._completer.setCompletionPrefix("")
            self._completer.complete()

    def _commit_current_item(self, index=None):
        if self._search_updating:
            return
        text = self.currentText()
        data = self.currentData()
        if data is not None:
            text = str(data)
        elif index is not None and isinstance(index, int) and 0 <= index < self.count():
            text = self.itemText(index)
        self.setCurrentText(text)
        if self.lineEdit():
            self.lineEdit().setText(text)
            self.lineEdit().selectAll()

    def _on_completion_activated(self, text):
        self._set_current_by_text(text)
        if self.lineEdit():
            self.lineEdit().setFocus(Qt.OtherFocusReason)
            self.lineEdit().setText(text)
            self.lineEdit().selectAll()

    def _set_current_by_text(self, text):
        for i in range(self.count()):
            if self.itemText(i) == text or str(self.itemData(i) or "") == text:
                self.setCurrentIndex(i)
                return True
        return False

    def _commit_typed_text(self):
        text = self.currentText().strip()
        if not text:
            return
        if self._set_current_by_text(text):
            return
        if not self._search_items:
            self.refresh_search_items()
        matches = [label for label, _ in self._search_items if match_pinyin(label, text)]
        if matches:
            self._set_current_by_text(matches[0])

    def eventFilter(self, obj, event):
        if obj is self.lineEdit() and event.type() in (QEvent.FocusIn, QEvent.MouseButtonPress):
            QTimer.singleShot(0, self._open_all)
        return super().eventFilter(obj, event)


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """Double spin box without wheel changes or +/- buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QDoubleSpinBox.NoButtons)

    def wheelEvent(self, event):
        event.ignore()


class NoWheelSpinBox(QSpinBox):
    """Integer spin box without wheel changes or +/- buttons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setButtonSymbols(QSpinBox.NoButtons)

    def wheelEvent(self, event):
        event.ignore()
