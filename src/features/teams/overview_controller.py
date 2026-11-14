# 拥有简报读取与来源弹窗，增量回填只作用于本轮当前队伍，离页撤销本 owner。
from concurrent.futures import CancelledError
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from src.domain.equipment.loadouts.plan_changes import SavedPlansChanged
from src.domain.teams.overview import SourceMode

from src.features.teams.loadouts.loadout_dialog import TeamLoadoutDialog
from src.features.teams.enhancement_history_dialog import TeamEnhancementHistoryDialog
from src.features.teams.report_matching.report_match_controller import ReportMatchController


class TeamOverviewController(QObject):
    source_changed = Signal(str)

    def __init__(self, *, service_provider, page, parent=None):
        super().__init__(parent)
        self._provider, self.page = service_provider, page
        self._service, self._dialog, self._teams = None, None, ()
        self._results = {}
        self._histories = {}
        self._requested = ()
        self._lifecycle_sequence = 0
        self._reader = ReportMatchController(self)
        self._reader.progress.connect(self._apply)
        self._reader.error.connect(self._failed)
        page.loadouts_requested.connect(self.configure_loadouts)
        page.enhancement_details_requested.connect(self.show_history)

    def refresh(self, teams):
        self._teams = tuple(teams)
        # 重建卡片时旧投影也失效；读取未完成或失败不能从详情入口误用上轮数值。
        self._results.clear()
        if not self._teams:
            self._reader.cancel()
            return
        if self._service is None:
            self._service = self._provider()
        visible = set(self.page.visible_team_ids())
        self._submit(sorted(self._teams, key=lambda team: team.team_id not in visible))

    def _submit(self, teams):
        service, frozen = self._service, tuple(teams)
        self._requested = frozen
        self._reader.submit(lambda cancelled, publish: service.load(frozen, cancelled=cancelled, publish=publish))

    def refresh_affected(self, team_ids):
        if self._service is None:
            return
        affected = [team for team in self._teams if team.team_id in team_ids]
        for team in affected:
            self._results.pop(team.team_id, None)
        # 替换读取会取消旧请求，但尚未交付的其他卡片仍须继续，不留永久“读取中”。
        pending = [team for team in self._teams if team.team_id not in self._results and team not in affected]
        if affected or pending:
            self._submit((*affected, *pending))

    def plans_saved(self, event):
        service = self._service
        if not isinstance(event, SavedPlansChanged) or service is None or not self._teams:
            return
        try:
            service.checkpoint()
        except CancelledError:
            return
        if ((event.account_id, event.generation) != (service.account_id, service.generation)
                or Path(event.database_path).resolve() != service.database_path.resolve()):
            return
        def affected(team):
            if team.team_id in event.team_ids:
                return True  # 游戏来源也需刷新本次新增的已保存增强。
            if not set(team.character_ids).intersection(event.character_ids):
                return False
            result = self._results.get(team.team_id)
            if result is None:
                return True
            if result.source.mode == SourceMode.GAME:
                return False  # 计算方案保存不是游戏装配完成。
            return not event.slot_ids or any(key in event.character_ids and slot in event.slot_ids
                                            for key, slot in result.source.bindings)
        ids = tuple(team.team_id for team in self._teams if affected(team))
        if not ids:
            return
        for key in ids:
            self._results.pop(key, None)
            self.page.show_overview_error(key, self.page.text("teams.card.plans_changed"))
        if self.page.isVisible():
            self.refresh_affected(ids)
        else:
            # 隐藏页仅撤销旧请求及标记失效；进入页面再读，不启动无主的后台刷新。
            self._reader.cancel()

    def _apply(self, overview):
        if overview.team in self._teams:
            self._results[overview.team.team_id] = overview
            self.page.show_overview(overview)

    def _failed(self, message):
        for team in self._requested:
            self.page.show_overview_error(team.team_id, message or self.page.text("teams.card.inputs_changed"))

    def configure_loadouts(self, team_id):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        team = next((team for team in self._teams if team.team_id == team_id), None)
        if team is None:
            return
        if self._service is None:
            self._service = self._provider()
        service = self._service
        sequence = self._lifecycle_sequence
        dialog = TeamLoadoutDialog(service=service, team=team, roles=self.page.role_presentations(),
            catalog=self.page.text_catalog, parent=self.page)
        self._dialog = dialog
        def finished(_result):
            if self._dialog is dialog:
                self._dialog = None
            changed = dialog.selection is not None
            dialog.deleteLater()
            if (self._service is service and team in self._teams
                    and sequence == self._lifecycle_sequence):
                try:
                    service.checkpoint()
                except CancelledError:
                    return
                # 关闭详情也可重试失败读取；取消不改来源，更不使毕业率上下文凭空失效。
                if changed:
                    self.source_changed.emit(team.team_id)
                if self.page.isVisible():
                    self.refresh_affected((team.team_id,))
                else:
                    self._results.pop(team.team_id, None)
        dialog.finished.connect(finished)
        dialog.open()

    def deactivate(self):
        # 离页先撤销详情回调的刷新权，再关子窗；否则取消也会重新启动后台读取。
        self._lifecycle_sequence += 1
        self._reader.cancel()
        for dialog in tuple(self._histories.values()):
            dialog.reject()
        if self._dialog is not None:
            self._dialog.reject()

    def reset_account(self):
        self.deactivate()
        self._service = None
        self._teams = ()
        self._requested = ()
        self._results.clear()

    def is_running(self):
        return (self._reader.is_running() or self._dialog is not None and self._dialog.is_running()
                or any(dialog.is_running() for dialog in self._histories.values()))

    def show_history(self, team_id):
        if team_id in self._histories:
            self._histories[team_id].raise_()
            return
        team = next((team for team in self._teams if team.team_id == team_id), None)
        if team is None:
            return
        if self._service is None:
            self._service = self._provider()
        dialog = TeamEnhancementHistoryDialog(service=self._service, team=team,
            names={key: role.name for key, role in self.page._roles.items()}, parent=self.page, catalog=self.page.text_catalog)
        self._histories[team_id] = dialog
        def finished(_):
            self._histories.pop(team_id, None)
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()
