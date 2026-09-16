# 在已有工作台同步快照时确认是否继续使用扫描模式。
import winsound
from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen
from src.storage.sqlite.user_data_dao import UserDataDao


SCAN_SOURCE_WARNING = (
    "你已进行过工作台-数据同步，已获取空幕数据，"
    "不建议再使用扫描模式，是否继续？"
)


def play_warning_sound() -> None:
    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)


class ScanSourceWarningDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._warning_sound_played = False
        self.setWindowTitle("扫描模式提示")
        layout = QVBoxLayout(self)
        self.message = QLabel(SCAN_SOURCE_WARNING, self)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

        self.buttons = QHBoxLayout()
        self.buttons.addStretch()
        self.continue_button = QPushButton("继续", self)
        self.continue_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("取消", self)
        self.cancel_button.clicked.connect(self.reject)
        self.cancel_button.setDefault(True)
        self.buttons.addWidget(self.continue_button)
        self.buttons.addWidget(self.cancel_button)
        layout.addLayout(self.buttons)
        fit_dialog_to_available_screen(self, QSize(520, 150))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._warning_sound_played:
            self._warning_sound_played = True
            play_warning_sound()


def current_snapshot_is_workbench_sync(
    database_path: str | Path,
    *,
    dao_factory=UserDataDao,
) -> bool:
    with dao_factory(database_path) as dao:
        summary = dao.current_inventory_summary()
    return bool(summary and summary.get("source") == "nte_core")


def confirm_scan_mode_after_workbench_sync(
    parent: QWidget | None,
    database_path: str | Path,
    *,
    dao_factory=UserDataDao,
    dialog_factory=ScanSourceWarningDialog,
) -> bool:
    if not current_snapshot_is_workbench_sync(database_path, dao_factory=dao_factory):
        return True
    return dialog_factory(parent).exec() == QDialog.Accepted


def restore_scan_mode_selection(button_group, button_id: int) -> None:
    previous_state = button_group.blockSignals(True)
    try:
        button = button_group.button(button_id)
        if button is not None:
            button.setChecked(True)
    finally:
        button_group.blockSignals(previous_state)
