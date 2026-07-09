# 显示使用教程图片和自动提示状态。
"""Onboarding and tutorial guide MainWindow methods."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from src.app import runtime
from src.app.theme import current_style_sheet
from src.features.scanning.file_lifecycle import IMAGE_EXTS
from src.utils.logger import logger

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ["_guide_image_files", "_maybe_show_quick_start", "_show_quick_start"]


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _guide_image_files(self):
    guide_dir = runtime.TEMPLATE_DIR / "guide"
    if not guide_dir.exists():
        return []
    def natural_key(path: Path):
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]
    return sorted([p for p in guide_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS], key=natural_key)


def _maybe_show_quick_start(self):
    seen_file = runtime.USER_CONFIG_DIR / "guide_seen.json"
    if not seen_file.exists():
        QTimer.singleShot(500, lambda: self._show_quick_start(auto=True))


def _show_quick_start(self, auto=False):
    images = self._guide_image_files()
    if not images:
        QMessageBox.warning(self, "使用教程", "未找到教程图片，请检查 config/templates/guide。")
        return

    dlg = QDialog(self)
    dlg.setWindowTitle("使用教程")
    dlg.setMinimumSize(760, 660)
    dlg.setStyleSheet(current_style_sheet())
    layout = QVBoxLayout(dlg)
    layout.setContentsMargins(14, 14, 14, 14)
    layout.setSpacing(10)

    image_label = QLabel()
    image_label.setAlignment(Qt.AlignCenter)
    image_label.setMinimumSize(720, 500)
    layout.addWidget(image_label, 1)

    index = {"value": 0}
    prev_btn = QPushButton("<")
    next_btn = QPushButton(">")
    page_label = QLabel()
    page_label.setAlignment(Qt.AlignCenter)

    def render():
        pix = QPixmap(str(images[index["value"]]))
        if not pix.isNull():
            image_label.setPixmap(pix.scaled(image_label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        page_label.setText(f"{index['value'] + 1} / {len(images)}")
        prev_btn.setEnabled(index["value"] > 0)
        next_btn.setEnabled(index["value"] < len(images) - 1)

    def move(delta):
        index["value"] = max(0, min(len(images) - 1, index["value"] + delta))
        render()

    prev_btn.clicked.connect(lambda: move(-1))
    next_btn.clicked.connect(lambda: move(1))
    nav = QHBoxLayout()
    nav.addWidget(prev_btn)
    nav.addWidget(page_label, 1)
    nav.addWidget(next_btn)
    layout.addLayout(nav)

    dont_show = QCheckBox("不再自动显示")
    dont_show.setChecked(auto)
    layout.addWidget(dont_show)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    render()
    dlg.exec()
    if dont_show.isChecked():
        try:
            with open(runtime.USER_CONFIG_DIR / "guide_seen.json", "w", encoding="utf-8") as f:
                json.dump({"seen": True}, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.warning(f"保存新手引导已读状态失败，下次可能会再次显示: {exc}")
