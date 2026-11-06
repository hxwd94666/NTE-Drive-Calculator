# 后台发现并复核队伍增强范围，显式战报优先于旧草稿，取消不提交局部选择。
from concurrent.futures import CancelledError
from dataclasses import replace

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.report_matching import MatchStatus, ReportCandidate, ReportRange, STATUS_LABELS, TeamReportMatch
from src.features.allocation.team_axis_cards import build_team_axis_card
from src.features.teams.report_match_controller import ReportMatchController
from src.features.teams.report_match_dialog import TeamReportMatchDialog, candidate_label
from src.services.allocation.team_axis_selection import load_team_choices, select_team_axes


class TeamAxisDialog(QDialog):
    def __init__(self, *, service, matching_service, role_names=(), teams=None, choices=None,
                 initial=None, initial_request=None, initial_team_id=None, initial_match=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("战报增强 · 选择队伍")
        self.selection, self.manage_teams = None, False
        self._service, self._matcher, self._choices = service, matching_service, choices
        self._initial, self._initial_request = initial, initial_request
        self._initial_team_id, self._initial_match = initial_team_id, initial_match
        self._rows, self._picker, self._closing_result = [], None, None
        self._mode = "load"
        self._controller = ReportMatchController(self)
        self._controller.result.connect(self._received)
        self._controller.error.connect(self._failed)
        self._controller.progress.connect(self._initial_progress)
        self._controller.drained.connect(self._drained)
        root = QVBoxLayout(self)
        hint = QLabel("最多选择两支人物互斥队伍；每队从正式阵容匹配结果选择战报范围。\n"
                      "阵容匹配后，正式计算仍会检查当前库存、固定队友与伤害输入。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.error = QLabel("正在读取队伍与历史；此步骤不要求当前库存。")
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        self._content = content
        self._cards = QVBoxLayout(content)
        self._cards.setAlignment(Qt.AlignTop)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        manage = QPushButton("前往队伍管理")
        manage.clicked.connect(self._manage)
        root.addWidget(manage)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("应用战报增强")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(self.buttons)
        fit_dialog_to_available_screen(self, QSize(900, 700))
        if teams is not None and choices is not None:
            self._populate(teams, choices)
        else:
            def load(cancelled, publish):
                if cancelled():
                    raise CancelledError
                return load_team_choices(self._service, tuple(role_names))
            self._controller.submit(load)

    def _received(self, value):
        if self._mode == "load":
            self._populate(*value)
        elif self._mode == "accept":
            self.selection = value
            self.done(QDialog.Accepted)
        else:
            self.error.setText("已定位本次明确指定的战报范围，请确认阵容匹配的队伍。")

    def _populate(self, teams, choices):
        self._choices = choices
        self._names = {key: name for name, key in choices.role_character_ids}
        for team in teams:
            row = build_team_axis_card(team, self._names)
            self._rows.append(row)
            row["choose"].clicked.connect(lambda checked=False, current=row: self._choose_report(current))
            row["check"].toggled.connect(self._update_selection)
            self._cards.addWidget(row["card"])
            self._restore(row)
        self.error.setText("没有队伍，请先在队伍管理配置。" if not teams else "请选择队伍及明确的战报范围。")
        self._update_selection()
        if self._initial_request is not None:
            self._mode = "initial"
            frozen = tuple((row["team"], row["match"].candidate) for row in self._rows if row["match"])
            def read(cancelled, publish):
                for team, candidate in frozen:
                    publish(self._matcher.match_page(team, (candidate,), cancelled=cancelled).results[0])
            self._controller.submit(read)

    def _restore(self, row):
        team = row["team"]
        old = next((binding for binding in self._initial.teams if binding.team == team), None) if self._initial else None
        if (self._initial_request is None and self._initial_match is not None
                and team.team_id == self._initial_team_id):
            self.set_report(team.team_id, self._initial_match)
            row["check"].setChecked(True)
            return
        request = self._initial_request or (old.axis.battle if old else None)
        if request is not None:
            scope = ReportRange(request.battle_record_id, "range" if request.start_us is not None else request.detail_scope or "all",
                                request.start_us, request.end_us)
            entry = next((entry for entry in self._choices.entries if entry.battle_record_id == scope.record_id), None)
            if entry:
                self.set_report(team.team_id, TeamReportMatch(team, ReportCandidate(entry, scope), MatchStatus.PENDING))
        selected = team.team_id == self._initial_team_id if self._initial_team_id is not None else old is not None
        if self._initial_request is None and selected:
            row["check"].setChecked(True)
            if old:
                row["anchor"].setCurrentIndex(row["anchor"].findData(old.axis.anchor_mode))

    def set_report(self, team_id, result):
        row = next(row for row in self._rows if row["team"].team_id == team_id)
        if result.team != row["team"]:
            raise ValueError("匹配结果的队伍修订已变化")
        row["match"] = result
        row["report"].setText(candidate_label(result.candidate) + "\n" + STATUS_LABELS[result.status]
                               + (" · " + result.reason if result.reason else ""))
        previous = row["anchor"].currentData()
        row["anchor"].clear()
        row["anchor"].addItem("战报原始配置", "original")
        if result.candidate.range.record_id in self._choices.active_edit_record_ids:
            row["anchor"].addItem("已启用编辑配置", "effective")
        if previous is not None and row["anchor"].findData(previous) >= 0:
            row["anchor"].setCurrentIndex(row["anchor"].findData(previous))

    def _initial_progress(self, result):
        self.set_report(result.team.team_id, result)
        self._update_selection()
        # 显式战报入口先呈现匹配队伍，不代替用户勾选；未知和差异队伍仍可打开详情。
        for row in sorted(self._rows, key=lambda row: not row["match"] or row["match"].status != MatchStatus.MATCHED):
            self._cards.removeWidget(row["card"])
            self._cards.addWidget(row["card"])

    def _choose_report(self, row):
        self._controller.cancel()
        dialog = TeamReportMatchDialog(service=self._matcher, team=row["team"], names=self._names,
            initial_range=row["match"].candidate.range if row["match"] else None, parent=self)
        self._picker = dialog
        try:
            if dialog.exec() == QDialog.Accepted and self._closing_result is None:
                self.set_report(row["team"].team_id, dialog.selection)
        finally:
            self._picker = None
            dialog.deleteLater()
            self._drained()

    def _update_selection(self):
        chosen = [row for row in self._rows if row["check"].isChecked()]
        used = {key for row in chosen for key in row["team"].character_ids}
        for row in self._rows:
            selected, team = row["check"].isChecked(), row["team"]
            conflicts = used.intersection(team.character_ids) if not selected else set()
            reason = ("请先在队伍管理补齐四名成员" if len(team.character_ids) != 4 else
                      "重复成员：" + "、".join(self._names.get(key, str(key)) for key in sorted(conflicts)) if conflicts else
                      "最多选择两支队伍" if not selected and len(chosen) >= 2 else "")
            row["check"].setEnabled(selected or not reason)
            for button in row["slots"].buttons:
                button.setEnabled(selected or not reason)
            match = row["match"]
            row["reason"].setText(reason or (match.reason if match and match.status != MatchStatus.MATCHED else ""))
            row["config"].setVisible(selected or self._initial_request is not None)
            color = "#58a6ff" if selected else "#30363d"
            row["card"].setStyleSheet(themed_style(
                f"QWidget#axisTeamCard{{border:1px solid {color};border-radius:8px;background:#0d1117;}}"))

    def _accept(self):
        selected = [row for row in self._rows if row["check"].isChecked()]
        if not selected or any(row["match"] is None for row in selected):
            self.error.setText("请选择一支或两支队伍，并为每队明确选择匹配战报范围。")
            return
        frozen = tuple((row["team"], row["match"].candidate, row["anchor"].currentData()) for row in selected)
        choices = self._choices
        self._mode = "accept"
        self._content.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        def read(cancelled, publish):
            configured, digests = [], {}
            for team, candidate, anchor in frozen:
                result = self._matcher.match_page(team, (candidate,), cancelled=cancelled).results[0]
                if result.status != MatchStatus.MATCHED:
                    raise ValueError(result.reason or "正式阵容未匹配，请重新选择战报范围")
                scope = candidate.range
                digests[team.team_id] = result.source_input_digest
                configured.append((team, scope.record_id, scope.scope, anchor, scope.start_us, scope.end_us))
            if cancelled():
                raise CancelledError
            selection = select_team_axes(self._service, choices, configured)
            return replace(selection, teams=tuple(replace(binding, axis=replace(binding.axis,
                expected_source_input_digest=digests[binding.team.team_id])) for binding in selection.teams))
        self._controller.submit(read)

    def _failed(self, error):
        self._content.setEnabled(True)
        self.error.setText(error)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    def _manage(self):
        self.manage_teams = True
        self.reject()

    def done(self, result):
        self._closing_result = result
        self._controller.close()
        if self.is_running():
            self.setEnabled(False)
            self.error.setText("正在结束本次后台读取…")
        if self._picker is not None:
            self._picker.reject()
        self._drained()

    def reject(self):
        self.done(QDialog.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.ignore() if self.is_running() else event.accept()

    def is_running(self):
        return self._controller.is_running() or self._picker is not None

    def _drained(self):
        if self._closing_result is not None and not self.is_running():
            super().done(self._closing_result)
