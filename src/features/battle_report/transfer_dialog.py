# 提供战报包多选、账号命名及导入导出弹窗。

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report_transfer import BattleReportTransferEntry


def _local_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


class BattleReportTransferDialog(QDialog):
    account_name_save_requested = Signal(str)
    export_requested = Signal(object)
    import_requested = Signal()

    def __init__(self, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("导出 / 读取战报包")
        self.setMinimumSize(900, 520)
        self.resize(1080, 680)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = QLabel("战报包")
        title.setStyleSheet(themed_style(
            "font-size:18px;font-weight:700;color:#f0f6fc"
        ))
        title_row.addWidget(title)
        title_row.addStretch()
        self.count_label = QLabel("0 场可导出")
        self.count_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)

        description = QLabel(
            "导出为应用专用、带完整性校验的压缩 .ntebr 文件；读取时会校验、解压，"
            "并在一个事务中导入当前账号的战报数据库。"
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(description)

        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("当前账号昵称"))
        self.account_name_edit = QLineEdit()
        self.account_name_edit.setPlaceholderText("账号昵称不能为空")
        self.account_name_edit.setClearButtonEnabled(True)
        account_row.addWidget(self.account_name_edit, 1)
        self.save_name_button = QPushButton("保存昵称")
        self.save_name_button.setObjectName("btnAction")
        self.save_name_button.clicked.connect(
            lambda: self.account_name_save_requested.emit(
                self.account_name_edit.text()
            )
        )
        account_row.addWidget(self.save_name_button)
        layout.addLayout(account_row)

        selection_row = QHBoxLayout()
        self.select_all_button = QPushButton("全选")
        self.clear_selection_button = QPushButton("取消选择")
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(
            lambda: self._set_all_checked(False)
        )
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.clear_selection_button)
        selection_row.addStretch()
        layout.addLayout(selection_row)

        self.empty_label = QLabel("当前账号还没有可导出的历史战报。")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:13px;padding:28px"
        ))
        layout.addWidget(self.empty_label)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ("选择", "采集时间", "玩法", "范围", "完整性 / cursor", "保留状态", "战报 ID")
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        for column in (0, 1, 2, 3, 5, 6):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.table.setColumnWidth(0, 60)
        self.table.setColumnWidth(1, 168)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 145)
        self.table.setColumnWidth(5, 90)
        self.table.setColumnWidth(6, 80)
        layout.addWidget(self.table, 1)

        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(themed_style(
            "color:#ff7b72;background:#da363322;border:1px solid #da3633;"
            "border-radius:6px;padding:7px"
        ))
        self.error_label.hide()
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.import_button = QPushButton("读取战报包")
        self.import_button.clicked.connect(self.import_requested)
        actions.addWidget(self.import_button)
        actions.addStretch()
        self.export_button = QPushButton("导出已选战报")
        self.export_button.setObjectName("btnPrimary")
        self.export_button.clicked.connect(
            lambda: self.export_requested.emit(self.selected_report_ids())
        )
        actions.addWidget(self.export_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        fit_dialog_to_available_screen(self, QSize(1080, 680))

    def set_account_name(self, value: str) -> None:
        self.account_name_edit.setText(value)
        self.account_name_edit.setModified(False)

    def set_entries(self, entries: tuple[BattleReportTransferEntry, ...]) -> None:
        self.table.setRowCount(len(entries))
        self.table.setVisible(bool(entries))
        self.empty_label.setVisible(not entries)
        self.count_label.setText(f"{len(entries)} 场可导出")
        self.export_button.setEnabled(bool(entries))
        self.select_all_button.setEnabled(bool(entries))
        self.clear_selection_button.setEnabled(bool(entries))
        for row, entry in enumerate(entries):
            selector = QTableWidgetItem()
            selector.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            selector.setCheckState(Qt.Unchecked)
            selector.setData(Qt.UserRole, entry.battle_record_id)
            selector.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(row, 0, selector)
            self.table.setItem(row, 1, self._item(_local_time(entry.captured_at_utc)))
            self.table.setItem(row, 2, self._item(entry.gameplay_label))
            self.table.setItem(row, 3, self._item(entry.scope_label))
            completeness = (
                f"{entry.completeness_label} · {entry.cursor_label} · "
                f"摘要 {entry.total_hits} 命中"
            )
            self.table.setItem(row, 4, self._item(completeness))
            self.table.setItem(row, 5, self._item(entry.retention_label))
            self.table.setItem(row, 6, self._item(str(entry.battle_record_id)))
            self.table.setRowHeight(row, 42)

    def selected_report_ids(self) -> tuple[int, ...]:
        result = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                result.append(int(item.data(Qt.UserRole)))
        return tuple(result)

    def has_unsaved_account_name(self) -> bool:
        return self.account_name_edit.isModified()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def set_busy(self, busy: bool) -> None:
        for widget in (
            self.save_name_button,
            self.import_button,
            self.account_name_edit,
            self.table,
        ):
            widget.setEnabled(not busy)
        has_entries = self.table.rowCount() > 0
        self.select_all_button.setEnabled(not busy and has_entries)
        self.clear_selection_button.setEnabled(not busy and has_entries)
        self.export_button.setEnabled(not busy and has_entries)

    @staticmethod
    def _item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return item

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None:
                item.setCheckState(state)


__all__ = ["BattleReportTransferDialog"]
