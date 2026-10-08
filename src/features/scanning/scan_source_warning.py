# 按当前库存来源确认扫描或引导用户前往工作台同步。
import winsound
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QDialog, QWidget

from src.features.input_operation_entry import OperationRecommendationDialog
from src.services.inventory_source_capabilities import is_visual_inventory_source
from src.storage.sqlite.user_data_dao import UserDataDao


SCAN_SOURCE_WARNING = (
    "你已进行过工作台-数据同步，已获取空幕数据，"
    "不建议再使用扫描模式，是否继续？"
)
SCAN_SYNC_RECOMMENDATION = (
    "建议使用工作台-数据同步，可快速获取空幕背包，扫描仅作为兜底功能。是否前往开启？"
)


def play_warning_sound() -> None:
    winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)


class ScanSourceWarningDialog(OperationRecommendationDialog):
    def __init__(self, parent: QWidget | None = None, *, recommend_sync: bool = False) -> None:
        super().__init__(
            parent, title="扫描模式提示",
            message=SCAN_SYNC_RECOMMENDATION if recommend_sync else SCAN_SOURCE_WARNING,
            action_text="前往" if recommend_sync else "继续",
        )

    def _play_warning_sound(self) -> None:
        play_warning_sound()


def current_snapshot_is_workbench_sync(
    database_path: str | Path,
    *,
    dao_factory=UserDataDao,
) -> bool:
    with dao_factory(database_path) as dao:
        summary = dao.current_inventory_summary()
    return bool(summary and summary.get("source") == "nte_core")


def confirm_scan_mode_entry(
    parent: QWidget | None,
    database_path: str | Path,
    *,
    navigate_home: Callable[[], None],
    dao_factory=UserDataDao,
    dialog_factory=ScanSourceWarningDialog,
) -> bool:
    with dao_factory(database_path) as dao:
        summary = dao.current_inventory_summary()
    recommend_sync = not summary or is_visual_inventory_source(summary.get("source"))
    if recommend_sync:
        if dialog_factory(parent, recommend_sync=True).exec() == QDialog.Accepted:
            navigate_home()
            return False  # 前往只导航工作台，保留原扫描模式。
        return True  # 取消、关闭或 Esc 仅拒绝同步建议，允许进入所选模式。
    if summary.get("source") == "nte_core":
        return dialog_factory(parent).exec() == QDialog.Accepted
    return True


def restore_scan_mode_selection(button_group, button_id: int) -> None:
    previous_state = button_group.blockSignals(True)
    try:
        button = button_group.button(button_id)
        if button is not None:
            button.setChecked(True)
    finally:
        button_group.blockSignals(previous_state)
