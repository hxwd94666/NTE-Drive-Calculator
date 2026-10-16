# 用角色头像和卡带图片编辑账号匹配草稿；保存与后台图纸检查归共享 owner。
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import Qt, Signal, QSize, QSignalBlocker, QEvent
from PySide6.QtGui import QIcon, QPen, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QStackedWidget,
    QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)
from src.app.theme import current_style_sheet, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.services.identification.matching_settings import (
    effective_matching_suits, reset_matching_role, set_matching_suits,
)
from src.ui.widgets import match_pinyin


class ImageChoiceDelegate(QStyledItemDelegate):
    """焦点只画在整张卡片边缘，避免原生样式在文字区再叠一层方框。"""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        option.state &= ~QStyle.State_HasFocus
        option.showDecorationSelected = True

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        view = self.parent()
        if view.keyboard_focus_visible and view.hasFocus() and view.currentIndex() == index:
            painter.save()
            painter.setPen(QPen(option.palette.color(QPalette.Link), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(option.rect.adjusted(2, 2, -2, -2), 7, 7)
            painter.restore()


class ImageChoiceList(QListWidget):
    """原生图片列表提供键盘选择；列宽随视口均分，小屏只滚动列表内容。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.keyboard_focus_visible = False
        self.setViewMode(QListWidget.IconMode)
        self.setResizeMode(QListWidget.Adjust)
        self.setMovement(QListWidget.Static)
        self.setWordWrap(True)
        self.setSpacing(6)
        self.setIconSize(QSize(72, 72))
        self.setItemDelegate(ImageChoiceDelegate(self))
        # 一格滚轮约移动 36 逻辑像素，而不是跨过多行卡片；触控板保留像素滚动。
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(12)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setMinimumSize(180, 100)
        self.setStyleSheet(themed_style(
            "QListWidget{background:#0d1117;border:1px solid #30363d;border-radius:8px}"
            "QListWidget::item{background:#161b22;border:1px solid #30363d;border-radius:7px;padding:5px}"
            "QListWidget::item:selected{background:#1f6feb33;border:2px solid #58a6ff;color:#c9d1d9}"
            "QListWidget::item:hover{border-color:#58a6ff}"))

    def focusInEvent(self, event):
        self.keyboard_focus_visible = event.reason() in (
            Qt.TabFocusReason, Qt.BacktabFocusReason, Qt.ShortcutFocusReason)
        super().focusInEvent(event)
        self.viewport().update()

    def mousePressEvent(self, event):
        # 鼠标取消选择后不画焦点框；保留当前项，后续键盘导航仍从此处继续。
        self.keyboard_focus_visible = False
        super().mousePressEvent(event)
        self.viewport().update()

    def keyPressEvent(self, event):
        self.keyboard_focus_visible = True
        super().keyPressEvent(event)
        self.viewport().update()

    def viewportEvent(self, event):
        result = super().viewportEvent(event)
        if event.type() == QEvent.Resize:
            # 滚动条出现也会改变视口宽度，按实际可用宽度重排，避免空出一整列。
            width = max(1, self.viewport().width() - 2 * self.spacing())
            self.setGridSize(QSize(max(132, width // max(1, width // 154)), 148))
            for index in range(self.count()):
                self.item(index).setSizeHint(self.gridSize() - QSize(12, 12))
        return result


class _StatusLabel(QLabel):
    """常态不占提示行；读取、冲突和失败提示出现时才分配空间。"""

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))

    def clear(self):
        self.setText("")


class MatchingEditorDialog(QDialog):
    preview_requested = Signal(int, object)
    preview_cancelled = Signal()
    save_requested = Signal(object)
    dismiss_requested = Signal()
    reload_requested = Signal()
    management_save_requested = Signal(object)
    management_save_finished = Signal(bool)
    busy_changed = Signal(bool, bool)

    def __init__(self, catalog, parent=None, *, embedded=False):
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.Widget)
        self.text = catalog.render
        self.roles, self.suits, self.draft = (), {}, {"version": 1, "extra_suits": {}}
        self._artwork, self._icons = {"roles": {}, "suits": {}}, {}
        self._active_id, self._committing = None, False
        self.setWindowTitle(self.text("matching.title"))
        self.setStyleSheet(current_style_sheet())
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        self.stack = QStackedWidget()
        overview, editor = QWidget(), QWidget()
        self.stack.addWidget(overview)
        self.stack.addWidget(editor)
        overview_layout, editor_layout = QVBoxLayout(overview), QVBoxLayout(editor)
        for layout in (overview_layout, editor_layout):
            layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(self.text("matching.official_hint"))
        hint.setWordWrap(True)
        overview_layout.addWidget(hint)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText(self.text("matching.search"))
        self.search.setAccessibleName(self.text("matching.search"))
        self.reset_all = QPushButton(self.text("matching.reset_all"))
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.reset_all)
        overview_layout.addLayout(toolbar)
        self.role_list = ImageChoiceList()
        self.role_list.setAccessibleName(self.text("matching.role"))
        overview_layout.addWidget(self.role_list, 1)
        self.empty = QLabel(self.text("matching.no_roles"))
        self.empty.hide()
        overview_layout.addWidget(self.empty)
        toolbar = QHBoxLayout()
        self.reset = QPushButton(self.text("matching.reset"))
        self.suit_search = QLineEdit()
        self.suit_search.setClearButtonEnabled(True)
        self.suit_search.setPlaceholderText(self.text("matching.suit_search"))
        self.suit_search.setAccessibleName(self.text("matching.suit_search"))
        toolbar.addWidget(self.suit_search, 1)
        toolbar.addWidget(self.reset)
        editor_layout.addLayout(toolbar)
        self.suit_list = ImageChoiceList()
        self.suit_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.suit_list.setAccessibleName(self.text("matching.choose_suits"))
        editor_layout.addWidget(self.suit_list, 1)
        root.addWidget(self.stack, 1)
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        scope = QLabel(self.text("matching.scope_hint"))
        scope.setWordWrap(True)
        footer.addWidget(scope, 1)
        self.status = _StatusLabel(self.text("matching.loading"))
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.reload_button = QPushButton(self.text("matching.reload"))
        self.reload_button.clicked.connect(self.reload_requested)
        self.reload_button.hide()
        root.addWidget(self.reload_button)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText(self.text("matching.save"))
        self.buttons.button(QDialogButtonBox.Save).setObjectName("btnNew")
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.text("matching.cancel"))
        self.buttons.accepted.connect(lambda: self.save_requested.emit(deepcopy(self.draft)))
        self.buttons.rejected.connect(self._back_or_cancel)
        footer.addWidget(self.buttons)
        root.addWidget(self.footer)
        if embedded:
            self.buttons.button(QDialogButtonBox.Save).hide()
            self.buttons.button(QDialogButtonBox.Cancel).hide()
        self.search.textChanged.connect(self._filter_roles)
        self.suit_search.textChanged.connect(self._filter_suits)
        self.role_list.itemClicked.connect(self._open_role)
        self.role_list.itemActivated.connect(self._open_role)
        self.suit_list.itemSelectionChanged.connect(self._selection_changed)
        self.reset.clicked.connect(self._reset_role)
        self.reset_all.clicked.connect(self._reset_all)
        self.set_busy(True)
        if not embedded:
            fit_dialog_to_available_screen(self, QSize(760, 610))

    def set_busy(self, busy, *, committing=False):
        self._committing = committing
        self.stack.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Save).setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.Cancel).setEnabled(not committing)
        self.reload_button.setEnabled(not busy)
        self.busy_changed.emit(busy, committing)

    def _back_or_cancel(self):
        if self._active_id is not None and not self._committing:
            self.show_overview()
        else:
            self.reject()

    def load(self, data):
        self.roles, suits, _stored, projected, self._artwork = data
        self.suits, self.draft = dict(suits), deepcopy(projected)
        self._icons.clear()
        self.reload_button.hide()
        self.set_busy(False)
        self.show_overview()

    def _icon(self, kind, identity):
        key = (kind, identity)
        if key not in self._icons:
            path = self._artwork.get(kind, {}).get(identity)
            icon = QIcon(str(path)) if path else QIcon()
            self._icons[key] = icon if not icon.isNull() else self.style().standardIcon(QStyle.SP_FileIcon)
        return self._icons[key]

    def _suit_name(self, suit):
        return self.suits.get(suit, self.text("matching.unknown_suit", id=suit))

    def _selected_role(self):
        return next((r for r in self.roles if r[0] == self._active_id), None)

    def _filter_roles(self):
        self.role_list.clear()
        query = self.search.text().strip()
        for identity, name, default in self.roles:
            label = name or self.text("matching.missing_role", id=identity)
            if query and not match_pinyin(label, query) and query not in str(identity):
                continue
            values = effective_matching_suits(self.draft, identity, default)
            subtitle = "、".join(self._suit_name(s) for s in values if s)
            item = QListWidgetItem(self._icon("roles", identity), f"{label}\n{subtitle}", self.role_list)
            item.setSizeHint(self.role_list.gridSize() - QSize(12, 12))
            item.setData(Qt.UserRole, identity)
            item.setTextAlignment(Qt.AlignCenter)
            item.setToolTip(f"{label}\n{subtitle}")
        self.empty.setVisible(self.role_list.count() == 0)

    def _open_role(self, item):
        self._active_id = item.data(Qt.UserRole)
        self.stack.setCurrentIndex(1)
        self.buttons.button(QDialogButtonBox.Save).show()
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.text("matching.back"))
        self.buttons.button(QDialogButtonBox.Cancel).show()
        self.suit_search.clear()
        self._render_role()
        self.suit_list.setFocus(Qt.TabFocusReason if self.role_list.keyboard_focus_visible else Qt.MouseFocusReason)

    def show_overview(self):
        previous = self._active_id
        self.preview_cancelled.emit()
        self._active_id = None
        self.stack.setCurrentIndex(0)
        self._filter_roles()
        for index in range(self.role_list.count()):
            if self.role_list.item(index).data(Qt.UserRole) == previous:
                self.role_list.setCurrentRow(index)
                break
        self.buttons.button(QDialogButtonBox.Cancel).setText(self.text("matching.cancel"))
        self.buttons.button(QDialogButtonBox.Cancel).setVisible(not self.embedded)
        self.buttons.button(QDialogButtonBox.Save).setVisible(not self.embedded)
        self.status.clear()
        self.status.setToolTip("")

    def _render_role(self):
        row = self._selected_role()
        if row is None:
            return
        identity, name, default = row
        selected = set(effective_matching_suits(self.draft, identity, default)) - {None}
        with QSignalBlocker(self.suit_list):
            self.suit_list.clear()
            # 未知条目仍可清理，不因目录更新静默丢弃；未知角色不新增选择。
            available = set(self.suits) | selected if name else selected
            for suit in sorted(available, key=lambda key: (key not in self.suits, self._suit_name(key))):
                item = QListWidgetItem(self._icon("suits", suit), self._suit_name(suit), self.suit_list)
                item.setSizeHint(self.suit_list.gridSize() - QSize(12, 12))
                item.setData(Qt.UserRole, suit)
                item.setTextAlignment(Qt.AlignCenter)
                item.setToolTip(self._suit_name(suit))
                item.setSelected(suit in selected)
        self._filter_suits()
        self._request_preview()

    def _filter_suits(self):
        query = self.suit_search.text().strip()
        for index in range(self.suit_list.count()):
            item = self.suit_list.item(index)
            item.setHidden(bool(query and not match_pinyin(item.text(), query)))

    def _selection_changed(self):
        row = self._selected_role()
        if row is None:
            return
        identity, name, default = row
        # 搜索隐藏的已选项也属于草稿，过滤界面绝不缩小实际匹配集合。
        values = [self.suit_list.item(i).data(Qt.UserRole) for i in range(self.suit_list.count())
                  if self.suit_list.item(i).isSelected()]
        if not values and name:
            self._render_role()
            self.status.setText(self.text("matching.keep_one"))
            return
        if values:
            set_matching_suits(self.draft, identity, default, values)
        else:
            reset_matching_role(self.draft, identity)
        self._request_preview()

    def _request_preview(self):
        self.status.setText(self.text("matching.checking"))
        self.preview_requested.emit(self._active_id, deepcopy(self.draft))

    def _reset_role(self):
        if self._active_id is not None:
            reset_matching_role(self.draft, self._active_id)
            self._render_role()

    def _reset_all(self):
        if QMessageBox.question(self, self.text("matching.reset_all"), self.text("matching.reset_all_confirm"),
                                QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        self.draft = {"version": 1, "extra_suits": {}}
        self.show_overview()

    def show_preview(self, states):
        if self._active_id is None:
            return
        problems = [self.text("matching.combination_status", suit=self._suit_name(suit),
                             state=self.text("matching.state." + state))
                    for suit, state in states.items() if state != "matched"]
        self.status.setText(self.text("matching.preview_problems", count=len(problems)) if problems
                            else "")
        self.status.setToolTip("\n".join(problems))
        for index in range(self.suit_list.count()):
            item = self.suit_list.item(index)
            state = states.get(item.data(Qt.UserRole))
            detail = self.text("matching.state." + state) if state and state != "matched" else ""
            item.setToolTip(item.text() + ("\n" + detail if detail else ""))

    def reject(self):
        if not self._committing:
            if self._active_id is not None:
                self.show_overview()
            elif self.embedded:
                self.dismiss_requested.emit()
            else:
                super().reject()

    def closeEvent(self, event):
        if self._committing:
            event.ignore()
        else:
            # 标题栏关闭是丢弃整个窗口，不等同于卡带页的返回导航。
            QDialog.reject(self)
            event.accept()
