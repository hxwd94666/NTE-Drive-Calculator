# 展示账号战报历史及显式记录管理操作。
"""Account battle history list with explicit record-management actions."""

from __future__ import annotations

from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from datetime import datetime, timezone

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report.history.records import BattleReportHistoryEntry
from src.integrations.game_assets.catalog import GameUiAssetCatalog


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


def _scene_label(entry: BattleReportHistoryEntry, catalog=None) -> str:
    # 环境名称是正式游戏数据，原样保留；只有来源后缀与兜底说明经目录渲染。
    catalog = resolve_text_catalog(catalog)
    if entry.environment_name:
        source = "inferred" if entry.environment_source == "inferred" else "confirmed"
        return catalog.render(f"battle_report.history.scene.{source}", name=f"{entry.environment_name}")
    if entry.combat_context_kind != "abyss":
        return catalog.render("battle_report.history.scene.unknown")
    if entry.abyss_floor is None:
        return catalog.render("battle_report.history.scene.abyss")
    return catalog.render("battle_report.history.scene.abyss_floor", floor=f"{entry.abyss_floor}")


class BattleReportHistoryDialog(QDialog):
    view_requested = Signal(int)
    retention_toggle_requested = Signal(int, str)
    delete_requested = Signal(int)

    def __init__(
        self,
        *,
        game_ui_asset_root,
        parent: QWidget | None = None,
        text_catalog=None,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._asset_catalog = GameUiAssetCatalog(game_ui_asset_root)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.battle_report.init.self_5'))
        self.resize(1120, 650)
        self.setMinimumSize(920, 480)
        self._build()
        fit_dialog_to_available_screen(self)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.init.self_5'))
        title.setStyleSheet(themed_style("font-size:18px;font-weight:700;color:#f0f6fc"))
        layout.addWidget(title)
        description = plain_label(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.description')
        )
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(description)

        self.empty_label = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.empty_label'))
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet(
            themed_style("color:#8b949e;font-size:13px;padding:28px")
        )
        layout.addWidget(self.empty_label)

        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels(
            ("ID", resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.log_table_3'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.roles_table'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('teams.report.column.time'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('teams.report.column.scene'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_2'), resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.build.table_3'))
        )
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionsMovable(False)
        header.setMinimumSectionSize(36)
        for column in (0, 1, 2, 3, 4, 6, 7):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 42)
        self.table.setColumnWidth(1, 46)
        self.table.setColumnWidth(2, 236)
        self.table.setColumnWidth(3, 158)
        self.table.setColumnWidth(4, 140)
        self.table.setColumnWidth(6, 54)
        self.table.setColumnWidth(7, 188)
        layout.addWidget(self.table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def set_entries(self, entries: tuple[BattleReportHistoryEntry, ...]) -> None:
        self.table.setRowCount(len(entries))
        self.table.setVisible(bool(entries))
        self.empty_label.setVisible(not entries)
        for row, entry in enumerate(entries):
            self.table.setItem(row, 0, self._text_item(str(entry.battle_record_id)))
            catalog = resolve_text_catalog(getattr(self, 'text_catalog', None))
            kind = self._text_item(catalog.render(
                "battle_report.history.capture.complete" if entry.native_capture else "battle_report.history.capture.partial"))
            kind.setToolTip(plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.set_entries.kind')))
            self.table.setItem(row, 1, kind)
            self.table.setCellWidget(row, 2, self._characters_widget(entry))
            self.table.setItem(row, 3, self._text_item(_local_time(entry.saved_at_utc)))
            self.table.setItem(row, 4, self._text_item(_scene_label(entry, catalog)))
            summary = catalog.render(
                "battle_report.history.summary", damage=_format_number(entry.total_damage),
                dps=_format_number(entry.total_dps), seconds=f"{entry.duration_seconds:.1f}",
                hits=f"{entry.total_hits:,}")
            self.table.setItem(row, 5, self._text_item(summary))
            self.table.setCellWidget(row, 6, self._status_widget(entry, text_catalog=getattr(self, 'text_catalog', None)))
            self.table.setCellWidget(row, 7, self._actions_widget(entry))
            self.table.setRowHeight(row, 54)

    @staticmethod
    def _text_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        item.setToolTip(text)
        return item

    def _characters_widget(self, entry: BattleReportHistoryEntry) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 3, 6, 3)
        layout.setSpacing(3)
        if not entry.character_ids:
            empty = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.characters_widget.empty'))
            empty.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            layout.addWidget(empty)
        for character_id in entry.character_ids[:8]:
            icon_path = self._asset_catalog.character_icon(character_id)
            icon = plain_label()
            icon.setFixedSize(25, 25)
            icon.setAlignment(Qt.AlignCenter)
            icon.setToolTip(plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.characters_widget.icon', character_id=f'{character_id}')))
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
    def _status_widget(entry: BattleReportHistoryEntry, *, text_catalog=None) -> QWidget:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(5, 8, 5, 8)
        badge = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.status_widget.badge') if entry.retention_kind == "manual" else resolve_text_catalog(text_catalog).render('ui.battle_report.status_widget.badge_2'))
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
        layout.addWidget(badge)
        return widget

    def _actions_widget(self, entry: BattleReportHistoryEntry) -> QWidget:
        # 按正式战报 ID 发出意图；确认、账号复核、事务和保留上限由既有 Controller/Service 处理。
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(4, 5, 4, 5)
        layout.setSpacing(5)

        view = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.actions_widget.view'))
        view.setObjectName("btnPrimary")
        view.clicked.connect(
            lambda _checked=False, record_id=entry.battle_record_id: (
                self.view_requested.emit(record_id)
            )
        )
        toggle = TextActionButton(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.actions_widget.toggle') if entry.retention_kind == "manual" else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('common.save')
        )
        toggle.clicked.connect(
            lambda _checked=False, record_id=entry.battle_record_id,
            retention=entry.retention_kind: self.retention_toggle_requested.emit(
                record_id,
                retention,
            )
        )
        delete = TextActionButton(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('warehouse.management.rules.delete'))
        delete.setObjectName("btnDanger")
        delete.clicked.connect(
            lambda _checked=False, record_id=entry.battle_record_id: (
                self.delete_requested.emit(record_id)
            )
        )
        for button, width in ((view, 48), (toggle, 68), (delete, 48)):
            button.setStyleSheet("padding:2px 4px;font-size:11px")
            button.ensurePolished()
            # 行内按钮使用最终字号和完整文案定宽，给公共文本按钮的左右留白留足空间。
            button.setFixedSize(max(width, button.fontMetrics().horizontalAdvance(button.accessibleName()) + 34), 28)
            layout.addWidget(button)
        # 长语言补丁和“取消保存”同样完整；不足时扩操作列，不挤压成只有省略号的按钮。
        self.table.setColumnWidth(7, max(self.table.columnWidth(7), layout.sizeHint().width()))
        return widget
