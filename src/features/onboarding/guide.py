# 显示使用教程图片和自动提示状态。
"""Onboarding guide collaborator owned by the application shell."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.app.context import AppContext
from src.app.theme import current_style_sheet
from src.features.scanning.file_lifecycle import IMAGE_EXTS
from src.utils.logger import logger

__all__ = ["OnboardingGuide"]


class OnboardingGuide:
    """Own the tutorial dialog without installing methods on MainWindow."""

    def __init__(self, *, app_context: AppContext, parent: QWidget) -> None:
        self._app_context = app_context
        self._parent = parent

    def image_files(self) -> list[Path]:
        guide_dir = self._app_context.paths.template_dir / "guide"
        if not guide_dir.exists():
            return []

        def natural_key(path: Path) -> list[int | str]:
            return [
                int(part) if part.isdigit() else part.lower()
                for part in re.split(r"(\d+)", path.name)
            ]

        return sorted(
            [
                path
                for path in guide_dir.iterdir()
                if path.is_file() and path.suffix.lower() in IMAGE_EXTS
            ],
            key=natural_key,
        )

    def maybe_show(self) -> None:
        seen_file = self._app_context.account.user_config_dir / "guide_seen.json"
        if not seen_file.exists():
            QTimer.singleShot(500, lambda: self.show(auto=True))

    def show(self, auto: bool = False) -> None:
        images = self.image_files()
        if not images:
            QMessageBox.warning(
                self._parent,
                "使用教程",
                "未找到教程图片，请检查 config/templates/guide。",
            )
            return

        dialog = QDialog(self._parent)
        dialog.setWindowTitle("使用教程")
        dialog.setMinimumSize(760, 660)
        dialog.setStyleSheet(current_style_sheet())
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignCenter)
        image_label.setMinimumSize(720, 500)
        layout.addWidget(image_label, 1)

        index = {"value": 0}
        previous_button = QPushButton("<")
        next_button = QPushButton(">")
        page_label = QLabel()
        page_label.setAlignment(Qt.AlignCenter)

        def render() -> None:
            pixmap = QPixmap(str(images[index["value"]]))
            if not pixmap.isNull():
                image_label.setPixmap(
                    pixmap.scaled(
                        image_label.size(),
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation,
                    )
                )
            page_label.setText(f"{index['value'] + 1} / {len(images)}")
            previous_button.setEnabled(index["value"] > 0)
            next_button.setEnabled(index["value"] < len(images) - 1)

        def move(delta: int) -> None:
            index["value"] = max(
                0,
                min(len(images) - 1, index["value"] + delta),
            )
            render()

        previous_button.clicked.connect(lambda: move(-1))
        next_button.clicked.connect(lambda: move(1))
        navigation = QHBoxLayout()
        navigation.addWidget(previous_button)
        navigation.addWidget(page_label, 1)
        navigation.addWidget(next_button)
        layout.addLayout(navigation)

        dont_show = QCheckBox("不再自动显示")
        dont_show.setChecked(auto)
        layout.addWidget(dont_show)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        render()
        dialog.exec()
        if dont_show.isChecked():
            self._mark_seen()

    def _mark_seen(self) -> None:
        try:
            seen_file = (
                self._app_context.account.user_config_dir / "guide_seen.json"
            )
            seen_file.parent.mkdir(parents=True, exist_ok=True)
            seen_file.write_text(
                json.dumps({"seen": True}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning(
                f"保存新手引导已读状态失败，下次可能会再次显示: {exc}"
            )
