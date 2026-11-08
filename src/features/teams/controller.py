# 持有队伍页和当前编辑弹窗；账号切换撤销旧草稿，失败保留可重试编辑内容。
import sqlite3

from PySide6.QtCore import QObject, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from src.features.teams.editor import TeamEditor
from src.features.teams.axis_graduation_controller import TeamAxisGraduationController
from src.features.teams.axis_graduation_dialog import TeamAxisGraduationDialog
from src.features.teams.page import TeamPage
from src.services.teams.management import TeamSessionExpired
from src.domain.teams.report_matching import MatchStatus
from src.features.teams.report_match_dialog import TeamReportMatchDialog
from src.features.teams.overview_controller import TeamOverviewController


class TeamController(QObject):
    def __init__(self, *, service_provider, matching_service_provider=None, configure_enhancement=None,
                 overview_service_provider=None, axis_graduation_service_provider=None, parent=None, catalog=None):
        super().__init__(parent)
        self._service_provider = service_provider
        self._matching_service_provider = matching_service_provider
        self._configure_enhancement = configure_enhancement
        self.page = TeamPage(parent, catalog=catalog)
        self._overview = (TeamOverviewController(service_provider=overview_service_provider, page=self.page, parent=self)
                          if overview_service_provider is not None else None)
        self.axis_graduation = (TeamAxisGraduationController(service_provider=axis_graduation_service_provider, parent=self)
                                if axis_graduation_service_provider is not None else None)
        self._teams = {}
        self._dialog = None
        self._match_dialog = None
        self._graduation_dialog = None
        self._roles = {}
        self.page.create_requested.connect(self.create)
        self.page.refresh_requested.connect(self.refresh)
        self.page.edit_requested.connect(self.edit)
        self.page.delete_requested.connect(self.delete)
        self.page.match_requested.connect(self.match_reports)
        self.page.enhancement_requested.connect(self.configure_enhancement)
        self.page.graduation_requested.connect(self.open_graduation)
        if self.axis_graduation is not None:
            self.axis_graduation.display_ready.connect(self._graduation_display)
            self.axis_graduation.history_ready.connect(self._graduation_history)
            if self._overview is not None:
                self._overview.source_changed.connect(self.axis_graduation.invalidate_current)

    def refresh(self):
        try:
            service = self._service_provider()
            teams = service.list_teams()
            try:
                roles = service.roles()
                warning = ""
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
                roles, warning = (), f"角色资料暂不可用：{error}。已保存队伍仍保留。"
            service.checkpoint()
            self._teams = {team.team_id: team for team in teams}
            self._roles = {role.character_id: role.name for role in roles}
            self.page.show_teams(teams, roles)
            if self.axis_graduation is not None and self.axis_graduation.team is not None:
                current_team = self._teams.get(self.axis_graduation.team.team_id)
                if current_team is None:
                    self.axis_graduation.reset_account()
                elif current_team != self.axis_graduation.team:
                    self.axis_graduation.set_team(current_team)
                elif self.axis_graduation.can_check_validity and not self.axis_graduation.is_running():
                    # 返回队伍页只撤销“仍是当前”的展示断言；不为刷新页面启动分析或重新搜索。
                    self.axis_graduation.mark_unverified()
            if self.axis_graduation is not None and self.axis_graduation.team in teams:
                display = self.axis_graduation.display or (self.axis_graduation.history.display if self.axis_graduation.history else None)
                if display is not None:
                    self.page.show_axis_graduation(self.axis_graduation.team, display)
            if self._overview is not None:
                self._overview.refresh(teams)
            if warning:
                self.page.status.setText(warning)
            return not warning
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
            self.page.status.setText(f"已保存队伍加载失败：{error}")
            return False

    def create(self):
        self._edit(None)

    def edit(self, team_id):
        team = self._teams.get(team_id)
        if team is not None:
            self._edit(team)

    def _edit(self, team):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        service = self._service_provider()
        try:
            roles = service.roles()
            dialog = TeamEditor(roles=roles, team=team, parent=self.page)
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
            self.page.status.setText(f"暂时无法编辑队伍：{error}")
            return
        self._dialog = dialog

        def save():
            try:
                service.save(dialog.draft(), existing=team)
            except TeamSessionExpired:
                dialog.reject()
                return
            except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
                dialog.error.setText(f"未保存：{error}")
                return
            dialog.accept()
            if self.refresh():
                self.page.status.setText("队伍已保存")

        def finished(_result):
            self._dialog = None
            dialog.deleteLater()

        dialog.save_requested.connect(save)
        dialog.finished.connect(finished)
        dialog.open()

    def delete(self, team_id):
        team = self._teams.get(team_id)
        if team is None:
            return
        service = self._service_provider()
        box = QMessageBox(QMessageBox.Warning, "删除队伍", f"删除队伍“{team.name}”？\n角色、配装和战报不会被删除。",
                          QMessageBox.Yes | QMessageBox.Cancel, self.page)
        box.setTextFormat(Qt.PlainText)
        box.button(QMessageBox.Yes).setText("删除队伍")
        box.button(QMessageBox.Cancel).setText("取消")
        box.setDefaultButton(QMessageBox.Cancel)
        QApplication.beep()
        result = box.exec()
        box.deleteLater()
        if result != QMessageBox.Yes:
            return
        try:
            service.delete(team)
            if self.refresh():
                self.page.status.setText("队伍已删除")
        except (ValueError, RuntimeError, OSError, sqlite3.Error) as error:
            self.page.status.setText(f"未删除：{error}")

    def reset_account(self):
        self.deactivate()
        if self.axis_graduation is not None:
            self.axis_graduation.reset_account()
        if self._overview is not None:
            self._overview.reset_account()
        if self._dialog is not None:
            self._dialog.reject()
        self._teams.clear()
        self.page.clear_account()

    def _graduation_display(self, display):
        team = self.axis_graduation.team
        if team is not None:
            self.page.show_axis_graduation(team, display)

    def _graduation_history(self, view):
        team = self._teams.get(view.team_id)
        display = self.axis_graduation.display or view.display
        if team is not None and display is not None:
            self.page.show_axis_graduation(team, display)

    def open_graduation(self, team_id):
        team = self._teams.get(team_id)
        if team is None or self.axis_graduation is None:
            return
        if self._graduation_dialog is not None:
            self._graduation_dialog.raise_()
            return
        dialog = TeamAxisGraduationDialog(owner=self.axis_graduation, team=team, parent=self.page)
        dialog.axis_requested.connect(lambda: self.match_reports(team_id, graduation=True))
        self._graduation_dialog = dialog
        def finished(_result):
            self._graduation_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def plans_saved(self, event):
        if self.axis_graduation is not None:
            self.axis_graduation.plans_saved(event)
        if self._overview is not None:
            self._overview.plans_saved(event)

    def match_reports(self, team_id, *, graduation=False):
        team = self._teams.get(team_id)
        if team is None or self._matching_service_provider is None:
            return
        if self._match_dialog is not None:
            self._match_dialog.raise_()
            return
        owner = self._graduation_dialog if graduation else self.page
        if owner is None:
            return
        dialog = TeamReportMatchDialog(service=self._matching_service_provider(), team=team,
            names=self._roles, initial_range=owner.reference_axis if graduation else None, parent=owner)
        dialog.setModal(True)
        if graduation:
            owner.attach_axis_picker(dialog)
        self._match_dialog = dialog
        dialog.counts_changed.connect(lambda counts: self.page.show_match_counts(team, counts))
        def finished(result):
            self._match_dialog = None
            selection = dialog.selection
            dialog.deleteLater()
            if result == QDialog.Accepted:
                if graduation:
                    if (self._graduation_dialog is owner and self._teams.get(team_id) == team and selection is not None
                            and selection.team == team and selection.status == MatchStatus.MATCHED):
                        owner.set_reference_axis(selection.candidate.range)
                elif self._configure_enhancement is not None:
                    self._configure_enhancement(team, selection)
        dialog.finished.connect(finished)
        dialog.open()

    def configure_enhancement(self, team_id):
        team = self._teams.get(team_id)
        if team is not None and self._configure_enhancement is not None:
            self._configure_enhancement(team)

    def deactivate(self):
        if self._graduation_dialog is not None:
            self._graduation_dialog.reject()
        if self.axis_graduation is not None and self.axis_graduation.is_running():
            self.axis_graduation.cancel()
        if self._overview is not None:
            self._overview.deactivate()
        if self._match_dialog is not None:
            self._match_dialog.reject()

    def is_running(self):
        return (self.axis_graduation is not None and self.axis_graduation.is_running()
                or self._match_dialog is not None and self._match_dialog.is_running()
                or self._overview is not None and self._overview.is_running())
