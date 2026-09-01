# 展示账号战报历史及显式记录管理操作。
"""Account battle history list with explicit record-management actions."""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.i18n import tr
from src.app.theme import themed_style
from src.domain.battle_report import BattleReportHistoryEntry
from src.services.game_ui_asset_catalog import GameUiAssetCatalog


def _format_number(value: float) -> str:
    return f"{value:,.0f}"


def _local_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return value


def _scene_label(entry: BattleReportHistoryEntry) -> str:
    if entry.combat_context_kind != "abyss":
        return tr("未知场景")
    if entry.abyss_floor is None:
        return tr("深渊")
    return tr("深渊 · 第 {floor} 层", floor=entry.abyss_floor)


class BattleReportHistoryDialog(QDialog):
    view_requested = Signal(int)
    retention_toggle_requested = Signal(int, str)
    delete_requested = Signal(int)

    def __init__(
        self,
        *,
        game_ui_asset_root,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._asset_catalog = GameUiAssetCatalog(game_ui_asset_root)
        self.setWindowTitle(tr("历史战报"))
        self.resize(1120, 650)
        self.setMinimumSize(920, 480)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel(tr("历史战报"))
        title.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#f0f6fc"))
        layout.addWidget(title)
        description = QLabel(
            tr("当前账号最多保留 100 条，第 101 条淘汰最旧自动记录；"
            "手动保存最多 50 条，第 51 条淘汰最旧手动记录。")
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(description)

        self.empty_label = QLabel(tr("当前账号还没有已保存的战报。"))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            themed_style("color:#8b949e;font-size:13px;padding:28px")
        )
        layout.addWidget(self.empty_label)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            (tr("角色"), tr("保存时间"), tr("场景"), tr("伤害摘要"), tr("状态"), tr("操作"))
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(60)
        for column in (0, 1, 2, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 218)
        self.table.setColumnWidth(1, 158)
        self.table.setColumnWidth(2, 145)
        self.table.setColumnWidth(4, 98)
        self.table.setColumnWidth(5, 250)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_entries(self, entries: tuple[BattleReportHistoryEntry, ...]) -> None:
        self.table.setRowCount(len(entries))
        self.table.setVisible(bool(entries))
        self.empty_label.setVisible(not entries)
        for row, entry in enumerate(entries):
            self.table.setCellWidget(row, 0, self._characters_widget(entry))
            self.table.setItem(row, 1, self._text_item(_local_time(entry.saved_at_utc)))
            self.table.setItem(row, 2, self._text_item(_scene_label(entry)))
            summary = (
                tr("伤害 {damage}  ·  DPS {dps}  ·  {seconds}s  ·  {hits} 命中",
                   damage=_format_number(entry.total_damage),
                   dps=_format_number(entry.total_dps),
                   seconds=f"{entry.duration_seconds:.1f}",
                   hits=f"{entry.total_hits:,}")
            )
            self.table.setItem(row, 3, self._text_item(summary))
            self.table.setCellWidget(row, 4, self._status_widget(entry))
            self.table.setCellWidget(row, 5, self._actions_widget(entry))
            self.table.setRowHeight(row, 54)

    @staticmethod
    def _text_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        return item

    def _characters_widget(self, entry: BattleReportHistoryEntry) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(3)
        if not entry.character_ids:
            empty = QLabel(tr("无角色数据"))
            empty.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            layout.addWidget(empty)
        for character_id in entry.character_ids[:8]:
            icon_path = self._asset_catalog.character_icon(character_id)
            icon = QLabel()
            icon.setFixedSize(25, 25)
            icon.setAlignment(Qt.AlignCenter)
            icon.setToolTip(tr("角色 ID：{cid}", cid=character_id))
            if icon_path is not None:
                icon.setPixmap(
                    QPixmap(str(icon_path)).scaled(
                        25,
                        25,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            else:
                icon.setText("?")
                icon.setStyleSheet(
                    themed_style(
                        "background:#21262d;color:#8b949e;border-radius:5px;"
                        "font-size:11px;font-weight:700"
                    )
                )
            layout.addWidget(icon)
        layout.addStretch()
        return widget

    @staticmethod
    def _status_widget(entry: BattleReportHistoryEntry) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 8, 5, 8)
        badge = QLabel(tr("手动保存") if entry.retention_kind == "manual" else tr("自动保存"))
        badge.setAlignment(Qt.AlignCenter)
        badge.setStyleSheet(
            themed_style(
                (
                    "background:#23863633;color:#7ee787;border:1px solid #238636;"
                    if entry.retention_kind == "manual"
                    else "background:#1f6feb22;color:#58a6ff;border:1px solid #1f6feb;"
                )
                + "border-radius:6px;padding:3px 6px;font-size:11px"
            )
        )
        badge.setToolTip(f"{entry.capability_level} · {entry.source_kind}")
        layout.addWidget(badge)
        return widget

    def _actions_widget(self, entry: BattleReportHistoryEntry) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 5, 4, 5)
        layout.setSpacing(5)

        view = QPushButton(tr("查看"))
        view.setObjectName("btnPrimary")
        view.clicked.connect(
            lambda _checked=False, record_id=entry.battle_record_id: (
                self.view_requested.emit(record_id)
            )
        )
        toggle = QPushButton(
            tr("取消保存") if entry.retention_kind == "manual" else tr("保存")
        )
        toggle.clicked.connect(
            lambda _checked=False, record_id=entry.battle_record_id,
            retention=entry.retention_kind: self.retention_toggle_requested.emit(
                record_id,
                retention,
            )
        )
        delete = QPushButton(tr("删除"))
        delete.setObjectName("btnDanger")
        delete.clicked.connect(
            lambda _checked=False, record_id=entry.battle_record_id: (
                self.delete_requested.emit(record_id)
            )
        )
        layout.addWidget(view)
        layout.addWidget(toggle)
        layout.addWidget(delete)
        return widget
