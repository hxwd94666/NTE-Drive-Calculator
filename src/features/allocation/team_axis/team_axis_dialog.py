# 后台发现并复核队伍增强范围，显式战报优先于旧草稿，取消不提交局部选择。
from concurrent.futures import CancelledError
from dataclasses import replace

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QGridLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.teams.dialog_support import ExplicitActionDialog
from src.domain.teams.report_matching import MatchStatus, ReportCandidate, ReportRange, TeamReportMatch
from src.domain.names.name_search import match_pinyin
from src.features.allocation.team_axis.team_axis_cards import build_team_axis_card
from src.features.teams.report_matching.report_match_controller import ReportMatchController
from src.features.teams.report_matching.report_match_dialog import TeamReportMatchDialog
from src.services.allocation.axis.team_axis_selection import load_team_choices, select_team_axes
from src.ui.common.text_catalog import ElidedTextLabel, plain_tooltip, resolve_text_catalog, set_button_text


class TeamAxisDialog(ExplicitActionDialog):
    def __init__(self, *, service, matching_service, role_names=(), teams=None, choices=None,
                 initial=None, initial_request=None, initial_team_id=None, initial_match=None, parent=None, catalog=None,
                 required_team=None):
        super().__init__(parent)
        self._catalog = resolve_text_catalog(catalog)
        text = self._catalog.render
        self._text, self._required_team = text, required_team
        self.setWindowTitle(text("teams.selection.title"))
        self.selection, self.manage_teams = None, False
        self._service, self._matcher, self._choices = service, matching_service, choices
        self._initial, self._initial_request = initial, initial_request
        self._initial_team_id = required_team.team_id if required_team is not None else initial_team_id
        self._initial_match = initial_match
        self._rows, self._picker, self._closing_result = [], None, None
        self._mode = "load"
        self._controller = ReportMatchController(self)
        self._controller.result.connect(self._received)
        self._controller.error.connect(self._failed)
        self._controller.progress.connect(self._initial_progress)
        self._controller.drained.connect(self._drained)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 14, 20, 14)
        root.setSpacing(10)
        self._header = QWidget(self)
        self._header_layout = QGridLayout(self._header)
        self._header_layout.setContentsMargins(0, 0, 0, 0)
        self._header_layout.setHorizontalSpacing(16)
        self._header_layout.setVerticalSpacing(4)
        self.heading = ElidedTextLabel(text('teams.selection.search_label'))
        self.heading.setStyleSheet('font-size:10px;font-weight:600;')
        self.search = QLineEdit(self._header)
        self.search.setPlaceholderText(text('teams.selection.search_placeholder'))
        self.search.setAccessibleName(text('teams.selection.search_accessible'))
        self.search.setClearButtonEnabled(True)
        self.heading.setBuddy(self.search)
        self.search.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.search.setEnabled(False)
        self.search.textChanged.connect(self._filter_rows)
        # 计数是完整状态，不按省略标签的最小宽度参与网格压缩；搜索框让出所需空间。
        self.selection_count = QLabel(text('teams.selection.selected_count', count=0, limit=2))
        self.selection_count.setTextFormat(Qt.PlainText)
        self.selection_count.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.selection_count.setStyleSheet('font-size:12px;font-weight:600;')
        # 正常指引、加载及异常复用一个区域，不重复堆叠三行说明。
        self.error = QLabel(text("teams.selection.loading"))
        self.error.setToolTip(plain_tooltip(text("teams.selection.validation_hint")))
        self.error.setAccessibleDescription(text("teams.selection.validation_hint"))
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        root.addWidget(self._header)
        scroll = QScrollArea()
        self._scroll = scroll
        scroll.viewport().installEventFilter(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        self._content = content
        self._cards = QVBoxLayout(content)
        self._cards.setAlignment(Qt.AlignTop)
        self._cards.setContentsMargins(0, 0, 4, 0)
        self._cards.setSpacing(8)
        self._filtered_empty = QLabel(text('teams.selection.no_search_results'), content)
        self._filtered_empty.setTextFormat(Qt.PlainText)
        self._filtered_empty.setWordWrap(True)
        self._filtered_empty.hide()
        self._cards.addWidget(self._filtered_empty)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        manage = QPushButton()
        self._manage_button = manage
        set_button_text(manage, text("teams.selection.manage"))
        manage.setAutoDefault(False)
        manage.clicked.connect(self._manage)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        # 确认动作固定在最右侧；标准按钮角色和 clicked/accepted 语义保持不变。
        self.buttons.setLayoutDirection(Qt.RightToLeft)
        self.buttons.setCenterButtons(False)
        self.buttons.button(QDialogButtonBox.Ok).setObjectName("btnNew")
        set_button_text(self.buttons.button(QDialogButtonBox.Ok), text("teams.selection.confirm"))
        set_button_text(self.buttons.button(QDialogButtonBox.Cancel), text("common.cancel"))
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        # 管理是导航而非提交动作，不能交给平台 ActionRole 重排到确认右侧。
        self._footer = QWidget(self)
        self._footer_layout = QGridLayout(self._footer)
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_layout.setSpacing(8)
        self._footer_layout.setColumnStretch(0, 1)
        self.list_count = ElidedTextLabel(text('teams.selection.visible_count', shown=0, total=0))
        self.list_count.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.list_count.setStyleSheet('font-size:11px;')
        root.addWidget(self._footer)
        self._wide = None
        self._reflow_chrome(True)
        QWidget.setTabOrder(self.search, manage)
        self.buttons.setFocusProxy(None)
        QWidget.setTabOrder(manage, self.buttons.button(QDialogButtonBox.Cancel))
        QWidget.setTabOrder(self.buttons.button(QDialogButtonBox.Cancel), self.buttons.button(QDialogButtonBox.Ok))
        self._count_timer = QTimer(self)
        self._count_timer.setSingleShot(True)
        self._count_timer.timeout.connect(self._refresh_counts)
        scroll.verticalScrollBar().valueChanged.connect(self._schedule_counts)
        scroll.verticalScrollBar().rangeChanged.connect(self._schedule_counts)
        # 固定四行密度的初始尺寸；回填、搜索和勾选均不触发顶层窗口缩放。
        target_size = fit_dialog_to_available_screen(self, QSize(880, 520))
        # 隐藏窗口的 Resize 可能延迟到首次显示；先按限位后的宽度重排，避免长译文先撑大最小尺寸。
        self._reflow_chrome(target_size.width() >= 700)
        self.layout().invalidate()
        self.layout().activate()
        self.resize(target_size)
        if teams is not None and choices is not None:
            self._populate(teams, choices)
        else:
            def load(cancelled, publish):
                if cancelled():
                    raise CancelledError
                return load_team_choices(self._service, tuple(role_names))
            self._controller.submit(load)

    def _reflow_chrome(self, wide):
        if self._wide == wide:
            return
        self._wide = wide
        for layout in (self._header_layout, self._footer_layout):
            while layout.count():
                layout.takeAt(0)
        header = self._header_layout
        header.setColumnStretch(1, 1)
        header.addWidget(self.heading, 0, 0)
        header.addWidget(self.selection_count, 0, 2, Qt.AlignRight)
        header.addWidget(self.search, 0 if wide else 1, 1 if wide else 0, 1, 1 if wide else 3)
        header.addWidget(self.error, 1 if wide else 2, 0, 1, 3)
        footer = self._footer_layout
        footer.addWidget(self.list_count, 0, 0, 1, 1 if wide else 3)
        if wide:
            footer.addWidget(self._manage_button, 0, 1)
            footer.addWidget(self.buttons, 0, 2)
        else:
            footer.addWidget(self._manage_button, 1, 1, 1, 2, Qt.AlignRight)
            footer.addWidget(self.buttons, 2, 1, 1, 2, Qt.AlignRight)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_footer_layout'):
            self._reflow_chrome(self.width() >= 700)
            self._schedule_counts()

    def eventFilter(self, watched, event):
        if watched is self._scroll.viewport() and event.type() in (QEvent.Resize, QEvent.Show):
            self._schedule_counts()
        return super().eventFilter(watched, event)

    def _schedule_counts(self, *_):
        if hasattr(self, '_count_timer') and self._closing_result is None:
            self._count_timer.start(0)

    def _filter_rows(self, *_):
        query = self.search.text().strip()
        matches = 0
        for row in self._rows:
            team = row['team']
            visible = match_pinyin(team.name, query) or any(
                match_pinyin(self._names.get(key, str(key)), query) for key in team.character_ids)
            row['card'].setVisible(visible)
            matches += visible
        self._filtered_empty.setVisible(bool(self._rows) and not matches)
        # 搜索只改本窗口展示；被隐藏的勾选、来源及战报范围仍保留，接受时仍统一核验。
        self._scroll.verticalScrollBar().setValue(0)
        self._schedule_counts()

    def _refresh_counts(self):
        if self._closing_result is not None:
            return
        viewport = self._scroll.viewport()
        full, partial = 0, 0
        for row in self._rows:
            card = row['card']
            if card.isHidden():
                continue
            rect = card.rect().translated(card.mapTo(viewport, card.rect().topLeft()))
            full += viewport.rect().contains(rect)
            partial += viewport.rect().intersects(rect)
        self.list_count.setText(self._text('teams.selection.visible_count', shown=full or partial, total=len(self._rows)))

    def _received(self, value):
        if self._mode == "load":
            self._populate(*value)
        elif self._mode == "accept":
            self.selection = value
            self.done(QDialog.Accepted)
        else:
            self.error.setText(self._text("teams.selection.located"))

    def _populate(self, teams, choices):
        if self._required_team is not None:
            # 单独计算不携带旧双队草稿；修订变化时也不默默选择新阵容。
            teams = tuple(team for team in teams if team == self._required_team)
        self._choices = choices
        self._names = {key: name for name, key in choices.role_character_ids}
        for team in teams:
            row = build_team_axis_card(team, self._names, catalog=self._catalog)
            self._rows.append(row)
            row["choose"].clicked.connect(lambda checked=False, current=row: self._choose_report(current))
            row["check"].toggled.connect(self._update_selection)
            self._cards.addWidget(row["card"])
            self._restore(row)
        self.error.setText(self._text("teams.calculate.team_changed") if not teams and self._required_team is not None
                           else self._text("teams.selection.empty") if not teams else self._text(
                               "teams.calculate.selection_hint" if self._required_team is not None else "teams.selection.hint"))
        self._update_selection()
        self.search.setEnabled(bool(teams))
        self._filter_rows()
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
        row["card"].set_report(result)
        previous = row["anchor"].currentData()
        row["anchor"].clear()
        row["anchor"].addItem(self._text("teams.selection.source_original"), "original")
        if result.candidate.range.record_id in self._choices.active_edit_record_ids:
            row["anchor"].addItem(self._text("teams.selection.source_effective"), "effective")
        if previous is not None and row["anchor"].findData(previous) >= 0:
            row["anchor"].setCurrentIndex(row["anchor"].findData(previous))
        self._schedule_counts()

    def _initial_progress(self, result):
        self.set_report(result.team.team_id, result)
        self._update_selection()
        # 显式战报入口先呈现匹配队伍，不代替用户勾选；未知和差异队伍仍可打开详情。
        position = self._scroll.verticalScrollBar().value()
        for row in sorted(self._rows, key=lambda row: not row["match"] or row["match"].status != MatchStatus.MATCHED):
            self._cards.removeWidget(row["card"])
            self._cards.addWidget(row["card"])
        self._cards.activate()
        self._scroll.verticalScrollBar().setValue(position)

    def _choose_report(self, row):
        self._controller.cancel()
        dialog = TeamReportMatchDialog(service=self._matcher, team=row["team"], names=self._names,
            initial_range=row["match"].candidate.range if row["match"] else None, parent=self, catalog=self._catalog)
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
        self.selection_count.setText(self._text('teams.selection.selected_count', count=len(chosen), limit=2))
        used = {key for row in chosen for key in row["team"].character_ids}
        for row in self._rows:
            selected, team = row["check"].isChecked(), row["team"]
            conflicts = used.intersection(team.character_ids) if not selected else set()
            reason = (self._text("teams.selection.incomplete") if len(team.character_ids) != 4 else
                      self._text("teams.selection.conflict", members="、".join(self._names.get(key, str(key)) for key in sorted(conflicts))) if conflicts else
                      self._text("teams.selection.limit") if not selected and len(chosen) >= 2 else "")
            row["check"].setEnabled(selected or not reason)
            for button in row["slots"].buttons:
                button.setEnabled(selected or not reason)
            match = row["match"]
            message = reason or (match.reason if match and match.status != MatchStatus.MATCHED else "")
            row["card"].set_state(selected, message)
            row["config"].setVisible(row["anchor"].count() > 1)
            row["choose"].setEnabled(not bool(reason))
        self._schedule_counts()

    def _accept(self):
        selected = [row for row in self._rows if row["check"].isChecked()]
        if not selected or any(row["match"] is None for row in selected):
            self.error.setText(self._text("teams.selection.need_report"))
            return
        frozen = tuple((row["team"], row["match"].candidate, row["anchor"].currentData()) for row in selected)
        choices = self._choices
        self._mode = "accept"
        self._content.setEnabled(False)
        self.search.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        def read(cancelled, publish):
            configured, digests = [], {}
            for team, candidate, anchor in frozen:
                result = self._matcher.match_page(team, (candidate,), cancelled=cancelled).results[0]
                if result.status != MatchStatus.MATCHED:
                    raise ValueError(result.reason or self._text("teams.selection.unmatched"))
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
        self.search.setEnabled(bool(self._rows))
        self.error.setText(error)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(True)

    def _manage(self):
        self.manage_teams = True
        self.reject()

    def done(self, result):
        self._closing_result = result
        self._count_timer.stop()
        self._controller.close()
        if self.is_running():
            self.setEnabled(False)
            self.error.setText(self._text("teams.selection.stopping"))
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
