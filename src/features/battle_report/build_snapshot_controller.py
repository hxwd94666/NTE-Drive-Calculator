# 编排战报角色配置副本的编辑、切换与角色页双向同步。
"""Qt coordinator for the battle build edit UI."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QDialog, QMessageBox, QWidget

from src.features.battle_report.build_snapshot_editor import (
    BattleBuildSnapshotEditorDialog,
)
from src.features.battle_report.page import BattleReportPage
from src.services.battle_report_history_service import BattleReportHistoryService


class BattleBuildSnapshotController:
    def __init__(
        self,
        *,
        page: BattleReportPage,
        dialog_parent: QWidget,
        service_provider: Callable[[], BattleReportHistoryService],
        record_id_provider: Callable[[], int | None],
        is_running: Callable[[], bool],
        reload_analysis: Callable[..., None],
        show_error: Callable[[str, Exception], None],
    ) -> None:
        self._page = page
        self._dialog_parent = dialog_parent
        self._service_provider = service_provider
        self._record_id_provider = record_id_provider
        self._is_running = is_running
        self._reload_analysis = reload_analysis
        self._show_error = show_error
        page.build_edit_requested.connect(self.edit)
        page.build_edit_activation_requested.connect(self.set_active)

    def refresh(self, battle_record_id: int) -> None:
        try:
            state = self._service_provider().load_build_edit_state(battle_record_id)
        except Exception:
            self._page.set_build_edit_state(
                has_edit=False,
                active=False,
                available=False,
            )
            return
        self._page.set_build_edit_state(
            has_edit=bool(state["has_edit"]),
            active=bool(state["is_active"]),
            available=bool(state["available"]),
        )

    def edit(self) -> None:
        record_id = self._editable_record_id()
        if record_id is None:
            return
        try:
            service = self._service_provider()
            editor_data = service.load_build_editor_data(record_id)
        except Exception as error:
            self._show_error("无法编辑本场角色配置", error)
            return
        dialog = BattleBuildSnapshotEditorDialog(
            editor_data,
            self._dialog_parent,
        )
        if dialog.exec() != QDialog.Accepted:
            return
        action = dialog.action()
        try:
            if action in {
                dialog.ACTION_IMPORT_CULTIVATION,
                dialog.ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT,
            }:
                include_equipment = (
                    action == dialog.ACTION_IMPORT_CULTIVATION_AND_EQUIPMENT
                )
                service.sync_role_page_to_build_edit(
                    record_id,
                    include_equipment=include_equipment,
                )
                self._reload_report(record_id)
                QMessageBox.information(
                    self._dialog_parent,
                    "同步完成",
                    (
                        "当前角色页养成及当前空幕/驱动已保存为本场修改副本并启用；"
                        "角色页数据没有被改写。"
                        if include_equipment
                        else "当前角色页养成已保存为本场修改副本并启用；"
                        "副本内已选空幕和驱动已保留。"
                    ),
                )
                return
            profiles = dialog.profiles()
            service.save_build_edit(record_id, profiles)
            if action == dialog.ACTION_SAVE_AND_SYNC_CULTIVATION:
                service.sync_build_edit_to_role_page(record_id)
        except Exception as error:
            self._show_error("保存本场角色配置失败", error)
            return
        self._reload_report(record_id)

    def set_active(self, active: bool) -> None:
        record_id = self._editable_record_id()
        if record_id is None:
            return
        try:
            self._service_provider().set_build_edit_active(record_id, active)
        except Exception as error:
            self._show_error("切换本场角色配置失败", error)
            return
        self._reload_report(record_id)
        if not active:
            self._page.show_report()

    def _editable_record_id(self) -> int | None:
        record_id = self._record_id_provider()
        if record_id is None or self._is_running():
            return None
        return int(record_id)

    def _reload_report(self, record_id: int) -> None:
        selected_range = self._page.analysis_range()
        self._reload_analysis(
            record_id,
            start_us=(selected_range or (None, None))[0],
            end_us=(selected_range or (None, None))[1],
            selected_character_id=self._page.analysis_character_id(),
            detail_level="hit",
        )
