# 展示账号养成历史、跨页选择、冻结全选集合及默认取消的批量删除确认。
from __future__ import annotations

from functools import partial

from PySide6.QtCore import QSignalBlocker, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QSplitter, QStyledItemDelegate, QStyle, QStyleOptionViewItem,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.theme import themed_style
from src.domain.cultivation_history import HistoryPage, HistoryRecord, HistorySelection, HistorySummary
from src.features.toolbox.cultivation.history_controller import CultivationHistoryController, HistoryOperationResult
from src.features.toolbox.cultivation.history_detail import CultivationHistoryDetail
from src.features.toolbox.cultivation.history_display import local_history_time, summary_text
from src.features.toolbox.cultivation.history_materials import IconLookup
from src.services.progression.history_service import CultivationHistoryService


# 此绘制委托只服务历史列表，与其布局 owner 同处，避免额外私有转发。
class HistoryCharacterDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        return QSize(0, max(24, option.fontMetrics.lineSpacing() + 6))

    def paint(self, painter, option, index) -> None:
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        text = styled.text
        styled.text = ""
        style = styled.widget.style() if styled.widget is not None else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, styled, painter, styled.widget)
        width = max(1, styled.rect.width() - 16)
        role = (QPalette.ColorRole.HighlightedText if styled.state & QStyle.StateFlag.State_Selected
                else QPalette.ColorRole.Text)
        painter.save()
        painter.setClipRect(styled.rect)
        painter.setFont(styled.font)
        group = QPalette.ColorGroup.Active if styled.state & QStyle.StateFlag.State_Enabled else QPalette.ColorGroup.Disabled
        painter.setPen(styled.palette.color(group, role))
        painter.drawText(styled.rect.adjusted(8, 0, -8, 0),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         styled.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, width))
        painter.restore()


class CultivationHistoryView(QWidget):
    back_requested = Signal()
    load_requested = Signal(object)

    def __init__(self, service: CultivationHistoryService, controller: CultivationHistoryController,
                 parent: QWidget, *, icon_lookup: IconLookup | None = None) -> None:
        super().__init__(parent)
        self._service, self._controller = service, controller
        self._selected: dict[str, HistorySelection] = {}
        self._page = 1
        self._total = 0
        self._list_request = self._selection_request = self._detail_request = self._delete_request = 0
        self._all_frozen = False
        self._restore_busy = False
        self._notice = ""
        self._current_record: HistoryRecord | None = None
        self._icon_lookup = icon_lookup
        self._split_initialized = False
        self._build()
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self.refresh)
        self._controller.completed.connect(self._completed)

    def _build(self) -> None:
        root = QVBoxLayout(self)
        heading = QHBoxLayout()
        back = QPushButton("‹ 返回计算", self)
        back.clicked.connect(self.back_requested)
        heading.addWidget(back)
        self._select_all = QPushButton("全选", self)
        self._select_all.setToolTip("全选当前筛选条件下的全部记录（包含其他分页）；未筛选时选择当前账号全部历史。")
        self._select_all.clicked.connect(self._select_filtered)
        heading.addWidget(self._select_all)
        clear = QPushButton("取消全选", self)
        clear.clicked.connect(self._clear_selection)
        heading.addWidget(clear)
        self._delete = QPushButton("删除所选", self)
        self._delete.clicked.connect(self._delete_selected)
        heading.addWidget(self._delete)
        self._count = QLabel("已选 0 条", self)
        heading.addWidget(self._count)
        heading.addStretch(1)
        self._mode = QComboBox(self)
        for label, mode in (("全部模式", None), ("单角色", "single"), ("多角色", "batch")):
            self._mode.addItem(label, mode)
        self._mode.currentIndexChanged.connect(self._filter_changed)
        heading.addWidget(self._mode)
        self._search = QLineEdit(self)
        self._search.setPlaceholderText("搜索角色 / 弧盘（支持拼音）")
        self._search.textChanged.connect(self._filter_changed)
        heading.addWidget(self._search, 1)
        root.addLayout(heading)
        self._table = QTreeWidget(self)
        self._table.setObjectName("cultivationHistoryList")
        self._table.setRootIsDecorated(False)
        self._table.setHeaderLabels(["选择", "最后计算时间", "当时体力", "角色配置"])
        header = self._table.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setColumnWidth(0, max(55, self.fontMetrics().horizontalAdvance("选择") + 24))
        self._table.setColumnWidth(1, self.fontMetrics().horizontalAdvance("2026-10-02 23:59:59") + 32)
        self._table.setItemDelegateForColumn(3, HistoryCharacterDelegate(self._table))
        self._table.setUniformRowHeights(True)
        self._table.setMinimumHeight(100)
        self._table.itemChanged.connect(self._checked_changed)
        self._table.currentItemChanged.connect(self._current_changed)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter = splitter
        splitter.setChildrenCollapsible(False)
        listing = QWidget(splitter)
        list_layout = QVBoxLayout(listing)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self._table, 1)
        navigation = QHBoxLayout()
        self._previous = QPushButton("上一页", self)
        self._previous.clicked.connect(lambda: self._turn_page(-1))
        navigation.addWidget(self._previous)
        self._page_label = QLabel(self)
        navigation.addWidget(self._page_label)
        self._next = QPushButton("下一页", self)
        self._next.clicked.connect(lambda: self._turn_page(1))
        navigation.addWidget(self._next)
        navigation.addStretch(1)
        list_layout.addLayout(navigation)
        preview = QWidget(splitter)
        preview_layout = QVBoxLayout(preview)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        detail_tools = QHBoxLayout()
        self._stamina = QLabel(preview)
        self._stamina.setObjectName("cultivationHistoryStaminaTotal")
        self._stamina.setTextFormat(Qt.TextFormat.PlainText)
        self._stamina.setStyleSheet(themed_style("color:#58a6ff;font-size:16px;font-weight:700;"))
        detail_tools.addWidget(self._stamina)
        detail_tools.addStretch(1)
        self._scope = QComboBox(self)
        self._scope.addItem("当时合计：全部", "all")
        self._scope.addItem("当时合计：仅体力", "stamina")
        self._scope.currentIndexChanged.connect(self._render_detail)
        detail_tools.addWidget(self._scope)
        self._load = QPushButton("加载配置", self)
        self._load.clicked.connect(self._load_configuration)
        self._load.setEnabled(False)
        detail_tools.addWidget(self._load)
        preview_layout.addLayout(detail_tools)
        self._detail = CultivationHistoryDetail(preview, icon_lookup=self._icon_lookup)
        preview_layout.addWidget(self._detail, 1)
        splitter.addWidget(listing)
        splitter.addWidget(preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([450, 550])
        root.addWidget(splitter, 1)
        self._status = QLabel(self)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setWordWrap(True)
        root.addWidget(self._status)
        self._status.hide()
        self._update_selection()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._split_initialized:
            self._split_initialized = True
            available = max(1, self._splitter.height() - self._splitter.handleWidth())
            self._splitter.setSizes([round(available * 0.45), round(available * 0.55)])

    def _filter(self) -> tuple[str | None, str]:
        return self._mode.currentData(), self._search.text()

    def _filter_changed(self, *_args: object) -> None:
        self._notice = ""
        self._selection_request = self._detail_request = self._list_request = 0
        self._page = 1
        self._current_record = None
        self._stamina.clear()
        self._detail.clear()
        self._load.setEnabled(False)
        self._table.clear()
        self._clear_selection()
        self._filter_timer.start()

    def refresh(self) -> None:
        self._filter_timer.stop()
        mode, search = self._filter()
        page = self._page
        self.set_message("正在读取当前账号历史。")
        self._list_request = self._controller.submit("list", partial(self._service.list, mode=mode, search=search, page=page))

    def _turn_page(self, delta: int) -> None:
        self._page = max(1, self._page + delta)
        self.refresh()

    def _checked_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if column != 0:
            return
        summary = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(summary, HistorySummary):
            return
        if self._selection_request:
            # A later all-selection reply must not overwrite newer manual intent.
            self._selection_request = 0
            self._all_frozen = False
        self._notice = ""
        if item.checkState(0) == Qt.CheckState.Checked:
            self._selected[summary.history_id] = HistorySelection(summary.history_id, summary.revision)
        else:
            self._selected.pop(summary.history_id, None)
        self._update_selection()

    def _clear_selection(self) -> None:
        self._selected.clear()
        self._all_frozen = False
        self._selection_request = 0
        self._apply_checks()

    def _apply_checks(self) -> None:
        with QSignalBlocker(self._table):
            for index in range(self._table.topLevelItemCount()):
                item = self._table.topLevelItem(index)
                summary = item.data(0, Qt.ItemDataRole.UserRole)
                item.setCheckState(0, Qt.CheckState.Checked if summary.history_id in self._selected else Qt.CheckState.Unchecked)
        self._update_selection()

    def _update_selection(self) -> None:
        self._count.setText(f"已选 {len(self._selected)} 条")
        self._count.setToolTip("全选集合已冻结，后续新增记录不会自动选中。" if self._all_frozen else "")
        self._delete.setEnabled(bool(self._selected) and not self._delete_request and not self._selection_request)
        self._select_all.setEnabled(not self._selection_request and not self._delete_request)

    def _select_filtered(self) -> None:
        if self._selection_request or self._delete_request:
            return
        mode, search = self._filter()
        self._selection_request = self._controller.submit("select_all", partial(self._service.select_all, mode=mode, search=search))
        self._update_selection()

    def _current_changed(self, current: QTreeWidgetItem | None, _previous: object = None) -> None:
        self._current_record = None
        self._stamina.clear()
        self._detail.clear()
        self._load.setEnabled(False)
        self._detail_request = 0
        if current is None:
            return
        summary = current.data(0, Qt.ItemDataRole.UserRole)
        history_id = summary.history_id
        self._detail_request = self._controller.submit("get", partial(self._service.get, history_id))

    def _render_detail(self, *_args: object) -> None:
        if self._current_record is not None:
            self._detail.set_record(self._current_record, scope=str(self._scope.currentData()))
            self._stamina.setText(self._detail.stamina_text)

    def _load_configuration(self) -> None:
        if self._current_record is not None:
            self.load_requested.emit(self._current_record)

    def _delete_selected(self) -> None:
        frozen = tuple(self._selected.values())
        if not frozen:
            return
        confirmation = QMessageBox(self)
        confirmation.setWindowTitle("删除养成历史")
        confirmation.setIcon(QMessageBox.Icon.Warning)
        confirmation.setText(f"删除当前账号所选 {len(frozen)} 条养成历史？")
        confirmation.setInformativeText("将删除历史配置和当时结果；当前计算草稿、角色档案与背包保持不变。")
        confirmation.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        confirmation.setDefaultButton(QMessageBox.StandardButton.Cancel)
        confirmation.adjustSize()
        fit_dialog_to_available_screen(confirmation)
        QApplication.beep()
        if confirmation.exec() != QMessageBox.StandardButton.Yes:
            return
        self._delete_request = self._controller.submit("delete", partial(self._service.delete, frozen))
        self.set_message("正在删除已确认的记录集合。")
        self._update_selection()

    def _completed(self, result: object) -> None:
        if not isinstance(result, HistoryOperationResult):
            return
        expected = {"list": self._list_request, "get": self._detail_request,
                    "select_all": self._selection_request, "delete": self._delete_request}
        if result.request_id != expected.get(result.operation):
            return
        if result.error_code is not None:
            self.set_message(result.message or "该请求已过期，请刷新历史。")
            if result.operation == "select_all":
                self._selection_request = 0
                self._update_selection()
            if result.operation == "delete":
                self._delete_request = 0
                if result.error_code == "conflict":
                    self._detail_request = 0
                    self._clear_selection()
                    self.refresh()
                    self._notice = result.message
                self._update_selection()
            return
        if result.operation == "list" and isinstance(result.value, HistoryPage):
            self._show_page(result.value)
        elif result.operation == "select_all":
            self._selection_request = 0
            self._selected = {item.history_id: item for item in result.value}
            self._all_frozen = True
            self._apply_checks()
        elif result.operation == "get":
            self._current_record = result.value
            self._load.setEnabled(isinstance(result.value, HistoryRecord) and not self._restore_busy)
            self._render_detail()
            self.set_message("" if result.value is not None else "记录已删除，请刷新列表。")
        elif result.operation == "delete":
            self._notice = f"删除完成，实际删除 {result.value} 条；当前计算草稿保持不变。"
            self._delete_request = 0
            self._detail_request = 0
            self._clear_selection()
            self._current_record = None
            self._stamina.clear()
            self._detail.clear()
            self._load.setEnabled(False)
            self.refresh()

    def _show_page(self, page: HistoryPage) -> None:
        self._detail_request = 0
        if page.page > 1 and not page.items and page.total <= (page.page - 1) * page.page_size:
            self._page = max(1, (page.total + page.page_size - 1) // page.page_size)
            self.refresh()
            return
        self._total = page.total
        self._current_record = None
        self._stamina.clear()
        self._detail.clear()
        self._load.setEnabled(False)
        with QSignalBlocker(self._table):
            self._table.clear()
            for summary in page.items:
                name, stamina = summary_text(summary)
                item = QTreeWidgetItem(["", local_history_time(summary.last_calculated_at_utc), stamina, name])
                item.setToolTip(1, f"{item.text(1)}（本机时间）\n原始 UTC：{summary.last_calculated_at_utc}")
                item.setToolTip(2, stamina)
                item.setToolTip(3, name)
                item.setTextAlignment(1, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                item.setTextAlignment(2, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
                item.setData(0, Qt.ItemDataRole.UserRole, summary)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if summary.history_id in self._selected else Qt.CheckState.Unchecked)
                self._table.addTopLevelItem(item)
        pages = max(1, (page.total + page.page_size - 1) // page.page_size)
        self._page_label.setText(f"第 {page.page}/{pages} 页，共 {page.total} 条")
        self._previous.setEnabled(page.page > 1)
        self._next.setEnabled(page.page < pages)
        self.set_message(self._notice)
        self._update_selection()

    def notify_saved(self, _summary: object) -> None:
        if self.isVisible():
            self.refresh()

    def set_message(self, message: str) -> None:
        self._status.setText(message)
        self._status.setVisible(bool(message))

    def set_restore_busy(self, busy: bool) -> None:
        self._restore_busy = busy
        self._load.setEnabled(self._current_record is not None and not busy)
