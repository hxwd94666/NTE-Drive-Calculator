# 阵容匹配列表保持正式校验与取消边界，紧凑筛选、空态和就近详情只拥有展示状态。
from src.ui.common.tooltip_policy import bind_elided_combo_tooltip
from PySide6.QtCore import QSize, Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QGridLayout,
    QHeaderView, QLabel, QScrollArea, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.teams.report_matching.manual_range_controls import ManualRangeControls
from src.features.teams.dialog_support import ExplicitActionDialog, LocalizedDisclosure
from src.domain.teams.report_matching import MatchStatus, ReportRange, TeamReportMatch
from src.features.teams.report_matching.report_match_controller import ReportMatchController
from src.features.teams.report_matching.report_match_presentation import candidate_label, candidate_parts, next_step, status_label
from src.ui.common.text_catalog import TextActionButton, plain_tooltip, resolve_text_catalog, set_button_text


class TeamReportMatchDialog(ExplicitActionDialog):
    counts_changed = Signal(object)

    def __init__(self, *, service, team, names=None, initial_range=None, parent=None, catalog=None):
        super().__init__(parent)
        self._catalog = resolve_text_catalog(catalog)
        self._text = text = self._catalog.render
        self.setWindowTitle(text('teams.report.title', team=team.name))
        self.service, self.team, self.names = service, team, names or {}
        self.selection = None
        self._initial_range = initial_range
        self._candidates, self._results = [], {}
        self._next_offset, self._mode = 0, 'discover'
        self._closing_result, self._warning, self._filter_wide = None, '', None
        self._controller = ReportMatchController(self)
        self._controller.result.connect(self._received)
        self._controller.progress.connect(self._progress)
        self._controller.error.connect(self._failed)
        self._controller.drained.connect(self._drained)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        hint = QLabel(text('teams.report.hint'))
        hint.setTextFormat(Qt.PlainText)
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.filters = QGridLayout()
        self.filters.setHorizontalSpacing(8)
        self.filters.setVerticalSpacing(6)
        self.status_filter, self.scene_filter = QComboBox(), QComboBox()
        self.status_filter.setAccessibleName(text('teams.report.filter.accessible'))
        for key in ('matched', 'all', 'related', 'unknown', 'conflict', 'error'):
            self.status_filter.addItem(text('teams.report.filter.' + key), key)
        self.scene_filter.setAccessibleName(text('teams.report.scene.accessible'))
        self.scene_filter.addItem(text('teams.report.scene.all'), None)
        self.refresh_button = TextActionButton(text('teams.report.refresh'))
        self.more_button = TextActionButton(text('teams.report.more'))
        root.addLayout(self.filters)
        self._reflow_filters(True)
        self.status = QLabel(text('teams.report.loading'))
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.list = QTreeWidget()
        self.list.setAccessibleName(text('teams.report.list_accessible'))
        self.list.setColumnCount(4)
        self.list.setHeaderLabels([text('teams.report.column.' + key) for key in ('range', 'scene', 'time', 'status')])
        self.list.setRootIsDecorated(False)
        self.list.setUniformRowHeights(True)
        self.list.setStyleSheet("QTreeView::item{padding:4px 0;}")
        self.list.setTextElideMode(Qt.ElideRight)
        self.list.setMinimumHeight(86)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.header().setStretchLastSection(False)
        for column in (0, 1):
            self.list.header().setSectionResizeMode(column, QHeaderView.Stretch)
        # 时间与短状态不随最长译文撑宽整张表；选中详情和提示保留完整文字。
        for column, width in ((2, 100), (3, 112)):
            self.list.header().setSectionResizeMode(column, QHeaderView.Fixed)
            self.list.setColumnWidth(column, width)
        root.addWidget(self.list, 1)
        self.empty = QWidget(self)
        empty = QVBoxLayout(self.empty)
        empty.addStretch()
        self.empty_title, self.empty_hint = QLabel(text('teams.report.empty.loading')), QLabel(text('teams.report.empty.hint'))
        for label in (self.empty_title, self.empty_hint):
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setAlignment(Qt.AlignCenter)
            empty.addWidget(label)
        self.show_all = TextActionButton(text('teams.report.empty.all'))
        self.show_all.clicked.connect(lambda: self.status_filter.setCurrentIndex(self.status_filter.findData('all')))
        empty.addWidget(self.show_all, 0, Qt.AlignHCenter)
        empty.addStretch()
        root.addWidget(self.empty, 1)
        self.list.hide()
        self.details = QLabel(text('teams.report.details_empty'))
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextSelectableByMouse)
        root.addWidget(self.details)
        manual = ManualRangeControls(text, self)
        self.manual_report, self.manual_scope = manual.report, manual.scope
        self.start, self.end, self.manual_check = manual.start, manual.end, manual.check
        for combo in (self.status_filter, self.scene_filter):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setMinimumWidth(0)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            bind_elided_combo_tooltip(combo)
        self.manual_scroll = QScrollArea()
        self.manual_scroll.setWidgetResizable(True)
        self.manual_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.manual_scroll.setFrameShape(QScrollArea.NoFrame)
        self.manual_scroll.setMinimumHeight(100)
        self.manual_scroll.setMaximumHeight(220)
        self.manual_scroll.setWidget(manual)
        manual.height_changed.connect(lambda height: self.manual_scroll.setMinimumHeight(min(220, max(100, height))))
        self.manual_section = LocalizedDisclosure(text('teams.report.manual.title'), self.manual_scroll, parent=self, catalog=self._catalog)
        self.manual_section.toggle.setToolTip('')
        self.manual_section.toggle.toggled.connect(self._fit_manual_section)
        root.addWidget(self.manual_section)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        set_button_text(self.buttons.button(QDialogButtonBox.Ok), text('teams.report.confirm'))
        self.buttons.button(QDialogButtonBox.Ok).setObjectName('btnNew')
        self.buttons.button(QDialogButtonBox.Ok).setToolTip(plain_tooltip(text('teams.report.confirm_hint')))
        set_button_text(self.buttons.button(QDialogButtonBox.Cancel), text('teams.report.close'))
        root.addWidget(self.buttons, 0, Qt.AlignRight)
        self.buttons.button(QDialogButtonBox.Ok).setFixedWidth(
            min(260, self.buttons.button(QDialogButtonBox.Ok).fontMetrics().horizontalAdvance(text('teams.report.confirm')) + 32))
        self.buttons.accepted.connect(self._choose)
        self.buttons.rejected.connect(self.reject)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.status_filter.currentIndexChanged.connect(self._render)
        self.scene_filter.currentIndexChanged.connect(self._render)
        self.refresh_button.clicked.connect(self.refresh)
        self.more_button.clicked.connect(self._discover)
        self.manual_report.currentIndexChanged.connect(self._manual_changed)
        self.manual_scope.currentIndexChanged.connect(self._scope_changed)
        self.start.valueChanged.connect(self._manual_time_changed)
        self.end.valueChanged.connect(self._manual_time_changed)
        self.manual_check.clicked.connect(self._check_manual)
        self._selection_changed()
        self._scope_changed()
        fit_dialog_to_available_screen(self, QSize(900, 500))
        QTimer.singleShot(0, self, self.refresh)

    def refresh(self):
        if self._closing_result is not None:
            return
        self._next_offset = 0
        self._discover()

    def _fit_manual_section(self, expanded):
        if expanded:
            self._manual_collapsed_size = self.size()
            body = self.manual_scroll.widget()
            body.layout().activate()
            height = min(220, max(100, body.minimumSizeHint().height()))
            self.manual_scroll.setMinimumHeight(height)
            desired = QSize(self.width(), self.height() + height + self.layout().spacing())
        else:
            desired = getattr(self, '_manual_collapsed_size', self.size())
        # 展开为输入留出真实高度；仅屏幕不足时滚动，不让新区域盖到独立底栏。
        fit_dialog_to_available_screen(self, desired)

    def _discover(self):
        if self._closing_result is not None:
            return
        self._mode = "discover"
        self.refresh_button.setEnabled(False)
        self.manual_check.setEnabled(False)
        offset, initial = self._next_offset, self._initial_range
        self.status.setText(self._text("teams.report.refreshing"))
        self.more_button.setEnabled(False)
        def read(cancelled, publish):
            discovery = self.service.discover(offset=offset, cancelled=cancelled)
            candidate = self.service.candidate(initial, cancelled=cancelled) if initial is not None else None
            return discovery, candidate, offset
        self._controller.submit(read)

    def _received(self, value):
        if self._mode == "discover":
            discovery, initial, offset = value
            self._warning = ""
            if offset == 0:
                self._candidates, self._results = [], {}
                self.manual_report.clear()
                self.manual_report.addItem(self._text("teams.report.manual.placeholder"), None)
                self.scene_filter.clear()
                self.scene_filter.addItem(self._text("teams.report.scene.all"), None)
            seen = {candidate.range for candidate in self._candidates}
            additions = []
            for candidate in ([initial] if initial is not None else []) + list(discovery.candidates):
                if candidate.range not in seen:
                    additions.append(candidate)
                    seen.add(candidate.range)
            self._candidates.extend(additions)
            self._next_offset = discovery.next_offset
            for candidate in additions:
                self._results[candidate.range] = TeamReportMatch(self.team, candidate, MatchStatus.PENDING)
                entry = candidate.entry
                if self.manual_report.findData(entry.battle_record_id) < 0:
                    self.manual_report.addItem(candidate_label(candidate, catalog=self._catalog), entry.battle_record_id)
                scene = entry.environment_name or ""
                if self.scene_filter.findData(scene) < 0:
                    self.scene_filter.addItem(scene or self._text("teams.report.scene.unknown"), scene)
            if offset == 0 and self._initial_range is not None:
                scope = self._initial_range
                self.manual_report.setCurrentIndex(self.manual_report.findData(scope.record_id))
                self.manual_scope.setCurrentIndex(self.manual_scope.findData(scope.scope))
                with QSignalBlocker(self.start), QSignalBlocker(self.end):
                    self.start.setValue((scope.start_us or 0) / 1_000_000)
                    self.end.setValue((scope.end_us or 0) / 1_000_000)
            self._render()
            self._mode = "match"
            self._controller.submit(lambda cancelled, publish: self.service.match_page(
                self.team, additions, limit=50, cancelled=cancelled, publish=publish))
        elif self._mode == "choose":
            result = value.results[0]
            self._progress(result)
            if result.status == MatchStatus.MATCHED:
                self.selection = result
                self.done(QDialog.Accepted)
            else:
                self._mode = "match"
                self.status.setText(result.reason or status_label(result.status, self._text))
        else:
            self._render()

    def _progress(self, result):
        self._results[result.candidate.range] = result
        self._render()

    def _failed(self, message):
        self._warning = self._text("teams.report.failed", reason=message)
        self.status.setText(self._warning)
        if not self._results:
            self.empty_title.setText(self._text('teams.report.empty.failed'))
            self.empty_hint.hide()
            self.show_all.hide()
        self.more_button.setEnabled(self._next_offset is not None)

    def _render(self, *_):
        current = self.list.currentItem()
        key = current.data(0, Qt.UserRole) if current else None
        scroll = self.list.verticalScrollBar().value()
        blocked = self.list.blockSignals(True)
        self.list.setUpdatesEnabled(False)
        self.list.clear()
        matched = sum(result.status == MatchStatus.MATCHED for result in self._results.values())
        confirmed = sum(result.status in {MatchStatus.MATCHED, MatchStatus.DIFFERENT} for result in self._results.values())
        unknown = len(self._results) - confirmed
        self.counts_changed.emit((matched if confirmed else None, len(self._results)))
        amount = self._text('teams.report.count.matched', count=matched) if confirmed else self._text('teams.report.count.unknown')
        self.status.setText(self._text('teams.report.count', total=len(self._results), matched=amount, unknown=unknown)
                            + self._warning)
        restored = None
        for result in sorted(self._results.values(), key=lambda row: row.status != MatchStatus.MATCHED):
            mode, scene = self.status_filter.currentData(), self.scene_filter.currentData()
            related = bool(set(result.candidate.entry.character_ids) & set(self.team.character_ids))
            visible = (mode == 'all' or mode == 'matched' and result.status == MatchStatus.MATCHED
                       or mode == 'related' and result.status != MatchStatus.MATCHED and related
                       or mode == 'unknown' and result.status in {MatchStatus.PENDING, MatchStatus.INSUFFICIENT}
                       or mode == 'conflict' and result.status == MatchStatus.CONFLICT
                       or mode == 'error' and result.status in {MatchStatus.ERROR, MatchStatus.UNAVAILABLE})
            if not visible or scene is not None and scene != (result.candidate.entry.environment_name or ''):
                continue
            scope, shown_scene, saved = candidate_parts(result.candidate, self._text)
            label = status_label(result.status, self._text)
            item = QTreeWidgetItem([scope, shown_scene, saved, label])
            item.setData(0, Qt.UserRole, result.candidate.range)
            for column in range(4):
                item.setToolTip(column, plain_tooltip(candidate_label(result.candidate, catalog=self._catalog)
                    + '\n' + label + ('\n' + result.reason if result.reason else '')
                    + ('\n' + self._text('teams.report.related_hint') if mode == 'related' else '')))
            self.list.addTopLevelItem(item)
            if result.candidate.range == key:
                restored = item
        # 进度回填不自动选择首行、不把筛选后的邻行当原选择，也不清零用户滚动位置。
        self.list.setCurrentItem(restored)
        if restored is None:
            self.list.clearSelection()
        self.list.doItemsLayout()
        self.list.verticalScrollBar().setValue(scroll)
        self.list.blockSignals(blocked)
        self.list.setUpdatesEnabled(True)
        present = self.list.topLevelItemCount() > 0
        self.list.setVisible(present)
        self.empty.setVisible(not present)
        empty_key = 'filtered' if self._results else 'none'
        self.empty_title.setText(self._text('teams.report.empty.' + empty_key))
        self.empty_hint.setVisible(bool(self._results))
        self.show_all.setVisible(bool(self._results) and self.status_filter.currentData() != 'all')
        self.more_button.setEnabled(self._next_offset is not None and not self._controller.is_running())
        self._selection_changed()

    def _selection_changed(self):
        item = self.list.currentItem()
        result = self._results.get(item.data(0, Qt.UserRole)) if item else None
        choosing = self._mode == 'choose' and self._controller.is_running()
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(
            result is not None and result.status == MatchStatus.MATCHED and not choosing)
        if result:
            missing = set(self.team.character_ids) - set(result.report_character_ids)
            extra = set(result.report_character_ids) - set(self.team.character_ids)
            names = lambda ids: '、'.join(self.names.get(key, self._text('teams.report.role', id=key))
                                          for key in sorted(ids)) or self._text('teams.report.members.none')
            lines = [candidate_label(result.candidate, catalog=self._catalog),
                     status_label(result.status, self._text) + (' · ' + result.reason if result.reason else '')]
            if missing or extra:
                lines.append(self._text('teams.report.members', missing=names(missing), extra=names(extra)))
            lines.append(next_step(result.status, self._text))
            self.details.setText('\n'.join(lines))
        else:
            self.details.setText(self._text('teams.report.details_empty'))

    def _choose(self):
        item = self.list.currentItem()
        result = self._results.get(item.data(0, Qt.UserRole)) if item else None
        if result is None or result.status != MatchStatus.MATCHED or self._mode == "choose" and self._controller.is_running():
            return
        self._mode = "choose"
        self.refresh_button.setEnabled(False)
        self.manual_check.setEnabled(False)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._controller.submit(lambda cancelled, publish: self.service.match_page(
            self.team, (result.candidate,), cancelled=cancelled))

    def _manual_changed(self):
        record = self.manual_report.currentData()
        entry = next((item.entry for item in self._candidates if item.entry.battle_record_id == record), None)
        self.manual_scope.clear()
        if entry:
            for label, key, enabled in ((self._text("teams.report.scope.all"), "all", not entry.has_first_half and not entry.has_second_half),
                                        (self._text("teams.report.scope.first"), "first", entry.has_first_half),
                                        (self._text("teams.report.scope.second"), "second", entry.has_second_half),
                                        (self._text("teams.report.manual.title"), "range", True)):
                if enabled:
                    self.manual_scope.addItem(label, key)

    def _scope_changed(self):
        self.manual_check.setEnabled(not self._controller.is_running()
            and self.manual_report.currentData() is not None and self.manual_scope.currentData() is not None)
        for widget in (self.start, self.end):
            widget.setEnabled(self.manual_report.currentData() is not None and not self._controller.is_running())

    def _manual_time_changed(self):
        # 编辑秒值即明确选择自定义区间；整场／半场不再把可见输入锁成不可编辑。
        # 初始化回填由 QSignalBlocker 保护，不把程序恢复旧范围误判为用户编辑。
        if self.manual_report.currentData() is not None and not self._controller.is_running():
            self.manual_scope.setCurrentIndex(self.manual_scope.findData('range'))

    def _check_manual(self):
        try:
            scope = self.manual_scope.currentData()
            chosen = ReportRange(self.manual_report.currentData(), scope,
                round(self.start.value() * 1_000_000) if scope == "range" else None,
                round(self.end.value() * 1_000_000) if scope == "range" else None)
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._mode = "manual"
        self.refresh_button.setEnabled(False)
        self.manual_check.setEnabled(False)
        self.status_filter.setCurrentIndex(self.status_filter.findData("all"))
        def read(cancelled, publish):
            candidate = self.service.candidate(chosen, cancelled=cancelled)
            return self.service.match_page(self.team, (candidate,), cancelled=cancelled, publish=publish)
        self._controller.submit(read)

    def done(self, result):
        self._closing_result = result
        self._controller.close()
        if self._controller.is_running():
            self.setEnabled(False)
            self.status.setText(self._text("teams.report.closing.accept" if result == QDialog.Accepted else "teams.report.closing.cancel"))
        self._drained()

    def reject(self):
        self.done(QDialog.Rejected)

    def closeEvent(self, event):
        self.reject()
        if self._controller.is_running():
            event.ignore()
        else:
            event.accept()

    def _drained(self):
        if self._closing_result is not None and not self._controller.is_running():
            super().done(self._closing_result)
        elif not self._controller.is_running():
            self.more_button.setEnabled(self._next_offset is not None)
            self.refresh_button.setEnabled(True)
            self._scope_changed()
            self._selection_changed()

    def is_running(self):
        return self._controller.is_running()

    def _reflow_filters(self, wide):
        if wide == self._filter_wide:
            return
        self._filter_wide = wide
        while self.filters.count():
            self.filters.takeAt(0)
        for column in range(4):
            self.filters.setColumnStretch(column, 0)
        for index, widget in enumerate((self.status_filter, self.scene_filter, self.refresh_button, self.more_button)):
            self.filters.addWidget(widget, 0 if wide else index // 2, index if wide else index % 2)
            self.filters.setColumnStretch(index if wide else index % 2, 2 if wide and index < 2 else 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_filters(self.width() >= 740)
