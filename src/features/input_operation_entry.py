# 调用组合根显式注入的用户入口说明与环境问题回调。
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog
from typing import Any
import winsound

from PySide6.QtCore import QSize
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen


class OperationRecommendationDialog(QDialog):
    """带一次系统提示音、默认取消的操作建议；确认本身不执行操作。"""

    def __init__(
        self, parent: QWidget | None, *, title: str, message: str, action_text: str,
        text_catalog=None,
    ) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._warning_sound_played = False
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        self.message = plain_label(message, self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        self.buttons = QHBoxLayout()
        self.buttons.addStretch()
        self.continue_button = QPushButton(action_text, self)
        self.continue_button.setAutoDefault(False)
        self.continue_button.clicked.connect(self.accept)
        self.cancel_button = TextActionButton(resolve_text_catalog(text_catalog).render('common.cancel'), self)
        self.cancel_button.clicked.connect(self.reject)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        self.buttons.addWidget(self.continue_button)
        self.buttons.addWidget(self.cancel_button)
        layout.addLayout(self.buttons)
        fit_dialog_to_available_screen(self, QSize(520, 150))

    def _play_warning_sound(self) -> None:
        winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._warning_sound_played:
            self._warning_sound_played = True
            self._play_warning_sound()


def confirm_operation_recommendation(
    parent: QWidget | None, *, title: str, message: str, action_text: str,
    text_catalog=None,
) -> bool:
    return OperationRecommendationDialog(
        parent, title=title, message=message, action_text=action_text,
        text_catalog=text_catalog,
    ).exec() == QDialog.Accepted


def request_input_entry(owner: Any, capability: str, label: str) -> bool:
    callback = getattr(owner, "operation_entry", None)
    return bool(callback(capability, label)) if callable(callback) else False


def show_input_unavailable(owner: Any, label: str, detail: str, target: str = "detection") -> None:
    callback = getattr(owner, "operation_unavailable", None)
    if callable(callback):
        callback(label, detail, target)


def show_sync_required(owner: Any, label: str) -> None:
    """将需要已开启同步的操作统一引导到工作台，不自动重试原操作。"""
    show_input_unavailable(
        owner, label,
        "同步连接未开启。\n请到工作台开启“自动同步”，进入游戏场景并等待同步就绪。",
        target="home",
    )
