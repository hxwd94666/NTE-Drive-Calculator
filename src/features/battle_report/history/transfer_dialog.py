# 提供战报包多选、账号命名及导入导出弹窗。

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from datetime import datetime, timezone

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report.history.transfer import BattleReportTransferEntry


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

    def __init__(self, *, parent: QWidget | None = None, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._selectors: dict[int, QCheckBox] = {}
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.battle_report.init.self_6'))
        self.setMinimumSize(900, 520)
        self.resize(1080, 680)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        title_row = QHBoxLayout()
        title = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.title'))
        title.setStyleSheet(themed_style(
            "font-size:18px;font-weight:700;color:#f0f6fc"
        ))
        title_row.addWidget(title)
        title_row.addStretch()
        self.count_label = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.count_label'))
        self.count_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        title_row.addWidget(self.count_label)
        layout.addLayout(title_row)

        description = plain_label(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.description_2')
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(description)

        account_row = QHBoxLayout()
        account_row.addWidget(plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.caption_7')))
        self.account_name_edit = QLineEdit()
        self.account_name_edit.setPlaceholderText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.account_name_edit'))
        self.account_name_edit.setClearButtonEnabled(True)
        account_row.addWidget(self.account_name_edit, 1)
        self.save_name_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.save_name_button'))
        self.save_name_button.setObjectName("btnAction")
        self.save_name_button.clicked.connect(
            lambda: self.account_name_save_requested.emit(
                self.account_name_edit.text()
            )
        )
        account_row.addWidget(self.save_name_button)
        layout.addLayout(account_row)

        selection_row = QHBoxLayout()
        self.select_all_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('selector.select_all'))
        self.clear_selection_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.clear_selection_button'))
        self.select_all_button.clicked.connect(lambda: self._set_all_checked(True))
        self.clear_selection_button.clicked.connect(
            lambda: self._set_all_checked(False)
        )
        selection_row.addWidget(self.select_all_button)
        selection_row.addWidget(self.clear_selection_button)
        selection_row.addStretch()
        layout.addLayout(selection_row)

        self.empty_label = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.empty_label_2'))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(themed_style(
            "color:#8b949e;font-size:13px;padding:28px"
        ))
        layout.addWidget(self.empty_label)

        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_4'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_5'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_6'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('teams.report.manual.scope'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_7'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_8'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_9'))
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

        self.error_label = plain_label()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(themed_style(
            "color:#ff7b72;background:#da363322;border:1px solid #da3633;"
            "border-radius:6px;padding:7px"
        ))
        self.error_label.hide()
        layout.addWidget(self.error_label)

        actions = QHBoxLayout()
        self.import_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.import_report_bundle.source_selected_filter'))
        self.import_button.clicked.connect(self.import_requested)
        actions.addWidget(self.import_button)
        actions.addStretch()
        self.export_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.export_button'))
        self.export_button.setObjectName("btnPrimary")
        self.export_button.clicked.connect(
            lambda: self.export_requested.emit(self.selected_report_ids())
        )
        actions.addWidget(self.export_button)
        close_button = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.close'))
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
        self._selectors.clear()
        self.table.setRowCount(len(entries))
        self.table.setVisible(bool(entries))
        self.empty_label.setVisible(not entries)
        self.count_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_entries.count_label', value=f'{len(entries)}'))
        self.export_button.setEnabled(bool(entries))
        self.select_all_button.setEnabled(bool(entries))
        self.clear_selection_button.setEnabled(bool(entries))
        for row, entry in enumerate(entries):
            selector_cell, selector = self._selector_widget()
            self._selectors[entry.battle_record_id] = selector
            self.table.setCellWidget(row, 0, selector_cell)
            self.table.setItem(row, 1, self._item(_local_time(entry.captured_at_utc)))
            self.table.setItem(row, 2, self._item(entry.gameplay_label))
            self.table.setItem(row, 3, self._item(entry.scope_label))
            completeness = (
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.battle_report.history.transfer_dialog.set_entries.fc85dd9d1', '{completeness_label} · {cursor_label} · 摘要 {total_hits} 命中', completeness_label=f'{entry.completeness_label}', cursor_label=f'{entry.cursor_label}', total_hits=f'{entry.total_hits}')
            )
            self.table.setItem(row, 4, self._item(completeness))
            self.table.setItem(row, 5, self._item(entry.retention_label))
            self.table.setItem(row, 6, self._item(str(entry.battle_record_id)))
            self.table.setRowHeight(row, 42)

    def selected_report_ids(self) -> tuple[int, ...]:
        # 导出意图只携带正式战报 ID；包校验、账号代次与事务复核沿用 Controller/Service。
        return tuple(
            report_id
            for report_id, selector in self._selectors.items()
            if selector.isChecked()
        )

    def has_unsaved_account_name(self) -> bool:
        return self.account_name_edit.isModified()

    def show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.show()

    def clear_error(self) -> None:
        self.error_label.clear()
        self.error_label.hide()

    def set_busy(self, busy: bool) -> None:
        # 忙碌状态只管理交互；禁用按钮不替代后台 worker 的取消或物理退场。
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

    @staticmethod
    def _selector_widget() -> tuple[QWidget, QCheckBox]:
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        selector = QCheckBox(cell)
        layout.addWidget(selector, 0, Qt.AlignCenter)
        return cell, selector

    def _set_all_checked(self, checked: bool) -> None:
        for selector in self._selectors.values():
            selector.setChecked(checked)


__all__ = ["BattleReportTransferDialog"]
