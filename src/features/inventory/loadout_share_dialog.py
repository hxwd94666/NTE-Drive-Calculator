# 在配装锁定或导入按钮右侧提供分享入口，复用可取消的角色面板与练度图片预览。
"""A modal, account-generation-bound owner for one read-only export."""

from __future__ import annotations

import re
from copy import deepcopy
from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event

from PySide6.QtCore import QPointF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QFileDialog, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout,
)

from src.app.theme import theme_color, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.integrations.loadout_share_image import render_loadout_share_png, save_share_png
from src.services.loadout_share_service import LoadoutShareRequest, build_loadout_share_panel
from src.utils.logger import logger


def share_icon(*, pressed: bool = False) -> QIcon:
    """Common three-node share symbol, drawn sharply at high DPI."""
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    color = QColor(theme_color("#4dd0e1" if pressed else "#8b949e"))
    painter.setPen(QPen(color, 4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawLine(QPointF(13, 24), QPointF(35, 11))
    painter.drawLine(QPointF(13, 24), QPointF(35, 37))
    painter.setBrush(QColor(theme_color("#21262d")))
    for x, y in ((12, 24), (36, 10), (36, 38)):
        painter.drawEllipse(QPointF(x, y), 5, 5)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    icon = QIcon()
    for mode in (QIcon.Normal, QIcon.Active, QIcon.Selected):
        icon.addPixmap(pixmap, mode)
    return icon


def loadout_action_button_style() -> str:
    """Transparent resting actions share hover, press and keyboard-focus affordances."""
    return themed_style(
        "QPushButton{background:transparent;border:1px solid transparent;border-radius:5px;padding:0}"
        "QPushButton:hover:enabled{background:#30363d;border-color:#4dd0e1}"
        "QPushButton:pressed:enabled{background:#21262d;border-color:#4dd0e1}"
        "QPushButton:focus:enabled{border-color:#4dd0e1}"
    )


class _ShareWorker(QThread):
    ready = Signal(bytes, str)
    failed = Signal(str)

    def __init__(self, request, cancelled: Event, parent: QDialog, generate=None) -> None:
        super().__init__(parent)
        self.request = request
        self.cancelled = cancelled
        self.generate = generate

    def run(self) -> None:
        try:
            if self.generate is None:
                panel = build_loadout_share_panel(self.request, cancelled=self.cancelled.is_set)
                data = render_loadout_share_png(panel, cancelled=self.cancelled.is_set)
                notes = '\n'.join(panel.notes)
            else:
                data, notes = self.generate(self.request, cancelled=self.cancelled.is_set)
            if not self.cancelled.is_set():
                self.ready.emit(data, notes)
        except CancelledError:
            pass
        except Exception as error:
            # Do not log role names, UIDs, raw account records, or exception payloads.
            logger.warning("分享图片生成失败 error_type={}", type(error).__name__)
            self.failed.emit("生成失败，请检查角色记录、配装数据与本地图片资源后重试。")


class LoadoutShareDialog(QDialog):
    def __init__(self, request, *, owner, generation: int, title: str = '角色面板分享',
                 filename: str | None = None, generate=None, ready_message: str | None = None) -> None:
        super().__init__(owner)
        self.setObjectName("loadoutShareDialog")
        self.setWindowTitle(title)
        self._export_filename = filename or f'{request.role_name}-{request.slot_name}'
        self._ready_message = ready_message or '预览已生成：此槽位配装 + 当前角色养成配置；无伤害测算。'
        self._owner = owner
        self._generation = generation
        self._request = request
        self._cancelled = Event()
        self._pending_result: int | None = None
        self._data = b""
        self._pixmap = QPixmap()
        layout = QVBoxLayout(self)
        self.status = QLabel('正在生成分享图片…')
        self.status.setWordWrap(True)
        self.status.setToolTip("分享图使用 MiSans 字体；字体与模板来源见随程序提供的资源许可通知。")
        layout.addWidget(self.status)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(False)
        self.preview = QLabel()
        self.preview.setObjectName("loadoutSharePreview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.preview)
        layout.addWidget(self.scroll, 1)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("仅本地生成，不上传账号数据"))
        actions.addStretch()
        self.copy_button = QPushButton("复制图片")
        self.copy_button.setObjectName("btnPrimary")
        self.save_button = QPushButton("保存 PNG")
        self.save_button.setObjectName("btnPrimary")
        close = QPushButton("关闭")
        for button in (self.copy_button, self.save_button):
            button.setEnabled(False)
            actions.addWidget(button)
        actions.addWidget(close)
        layout.addLayout(actions)
        self.copy_button.clicked.connect(self._copy)
        self.save_button.clicked.connect(self._save)
        close.clicked.connect(self.reject)
        self.worker = _ShareWorker(request, self._cancelled, self, generate)
        self.worker.ready.connect(self._loaded)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self._account_timer = QTimer(self)
        self._account_timer.setInterval(200)
        self._account_timer.timeout.connect(self._check_account)
        self._account_timer.start()
        fit_dialog_to_available_screen(self, QSize(800, 900))
        self.worker.start()

    def _current(self) -> bool:
        return not self._cancelled.is_set() and self._owner.app_context.generation == self._generation

    def _check_account(self) -> None:
        if not self._current():
            self.reject()

    def _loaded(self, data: bytes, notes: str = "") -> None:
        if not self._current():
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data, "PNG"):
            self._failed("生成图片解码失败，请重试。")
            return
        self._data = data
        self._pixmap = pixmap
        self.status.setText(self._ready_message)
        self.status.setToolTip(notes + "\n分享图使用 MiSans 字体；来源与许可见资源通知。")
        self._resize_preview()
        self.copy_button.setEnabled(True)
        self.save_button.setEnabled(True)

    def _failed(self, message: str) -> None:
        if self._current():
            self.status.setText(message)

    def _resize_preview(self) -> None:
        if self._pixmap.isNull():
            return
        width = min(self._pixmap.width(), max(120, self.scroll.viewport().width() - 22))
        scaled = self._pixmap.scaledToWidth(width, Qt.SmoothTransformation)
        self.preview.setPixmap(scaled)
        self.preview.setFixedSize(scaled.size())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_pixmap"):
            self._resize_preview()

    def _copy(self) -> None:
        if not self._current() or not self._data:
            return
        QApplication.clipboard().setImage(self._pixmap.toImage())
        self.status.setText("图片已复制，可直接粘贴分享。")

    def _save(self) -> None:
        if not self._current() or not self._data:
            return
        name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", self._export_filename)[:100]
        filename, _ = QFileDialog.getSaveFileName(self, '保存分享图片', name + ".png", "PNG 图片 (*.png)")
        if not filename or not self._current():
            return
        if not filename.casefold().endswith(".png"):
            filename += ".png"
            if Path(filename).exists() and QMessageBox.question(self, "覆盖图片", "此图片已存在，是否覆盖？", QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
        if not self._current():
            return
        try:
            save_share_png(filename, self._data)
        except OSError:
            self.status.setText("保存失败，请检查目标目录权限或选择其他位置。")
            return
        self.status.setText("PNG 已保存。")

    def _finished(self) -> None:
        if self._pending_result is not None:
            super().done(self._pending_result)

    def done(self, result: int) -> None:
        self._account_timer.stop()
        self._cancelled.set()
        self.copy_button.setEnabled(False)
        self.save_button.setEnabled(False)
        if self.worker.isRunning():
            self._pending_result = result
            self.status.setText("正在取消生成，请稍候…")
            return
        super().done(result)

    def closeEvent(self, event) -> None:
        if self.worker.isRunning():
            event.ignore()
            self.reject()
        else:
            super().closeEvent(event)


def loadout_share_button(owner, *, role_name: str, state: dict, score: float, size: int) -> QPushButton:
    # Keep the exact weights used to display this header, including old plans
    # whose item scores have to be reconstructed by the shared scoring service.
    config = deepcopy(owner.roles_db.get(role_name, {}))
    button = QPushButton()
    button.setObjectName("loadoutShareButton")
    button.setFixedSize(size, size)
    idle_icon, pressed_icon = share_icon(), share_icon(pressed=True)
    button.setIcon(idle_icon)
    button.pressed.connect(lambda: button.setIcon(pressed_icon))
    button.released.connect(lambda: button.setIcon(idle_icon))
    button.setIconSize(QSize(22, 22))
    button.setAccessibleName("分享角色面板")
    button.setToolTip("生成此配装的角色面板分享图（无伤害测算）")
    button.setStyleSheet(loadout_action_button_style())

    def opened() -> None:
        context = owner.app_context
        copied_state = deepcopy(state)
        if copied_state.get('_game_mode'):
            projection = copied_state.get('_game_projection')
            if projection is not None:
                copied_state['_official_items'] = deepcopy(list(projection.items))
        request = LoadoutShareRequest(
            user_database_path=Path(context.account.user_database_path),
            static_database_path=Path(context.paths.static_database_path),
            asset_root=Path(context.paths.game_ui_asset_root),
            config_dir=Path(context.paths.bundled_config_dir),
            character_id=int(state["_character_id"]) if state.get("_character_id") is not None else None,
            role_name=role_name,
            slot_name=str(state.get("_loadout_slot_name") or
                          ("当前装备" if state.get("_game_mode") else state.get("_display_name") or "主力")),
            score=score,
            state=copied_state, weights=config.get("weights", {}), main_weights=config.get("main_weights"),
            shape_areas=deepcopy(getattr(owner, "_shape_areas", {})),
        )
        dialog = LoadoutShareDialog(request, owner=owner, generation=context.generation)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    button.clicked.connect(opened)
    return button
