# 阵容匹配共用列表只展示正式范围与状态，手工时段也经同一后台预检。
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGridLayout, QGroupBox, QHeaderView,
    QLabel, QPushButton, QScrollArea, QSizePolicy, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.report_matching import (
    MatchStatus, ReportRange, STATUS_LABELS, TeamReportMatch,
)
from src.features.teams.report_match_controller import ReportMatchController
from src.features.teams.widgets import Disclosure


def candidate_label(candidate):
    entry = candidate.entry
    scene = entry.environment_name or (f"深渊 {entry.abyss_floor} 层" if entry.abyss_floor else "场景未记录")
    try:
        saved = datetime.fromisoformat(entry.saved_at_utc.replace("Z", "+00:00")).astimezone().strftime("%m-%d %H:%M")
    except ValueError:
        saved = entry.saved_at_utc
    return f"#{entry.battle_record_id} · {candidate.range.label} · {scene} · {saved}"


class TeamReportMatchDialog(QDialog):
    counts_changed = Signal(object)

    def __init__(self, *, service, team, names=None, initial_range=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("匹配战报 · " + team.name)
        self.service, self.team, self.names = service, team, names or {}
        self.selection = None
        self._initial_range = initial_range
        self._candidates = []
        self._results = {}
        self._next_offset = 0
        self._mode = "discover"
        self._closing_result = None
        self._warning = ""
        self._controller = ReportMatchController(self)
        self._controller.result.connect(self._received)
        self._controller.progress.connect(self._progress)
        self._controller.error.connect(self._failed)
        self._controller.drained.connect(self._drained)
        root = QVBoxLayout(self)
        hint = QLabel("查找与本队四人阵容相同的战报。匹配后还需检查计算条件。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        filters = QGridLayout()
        self.status_filter, self.scene_filter = QComboBox(), QComboBox()
        self.status_filter.setAccessibleName("战报匹配状态筛选")
        for text, key in (("阵容匹配", "matched"), ("全部状态", "all"), ("其他相关战报", "related"),
                          ("证据不足／待核验", "unknown"), ("范围冲突", "conflict"), ("组件／核验故障", "error")):
            self.status_filter.addItem(text, key)
        self.scene_filter.setAccessibleName("战报场景筛选")
        self.scene_filter.addItem("全部场景", None)
        self.refresh_button = QPushButton("重新核验")
        self.more_button = QPushButton("加载更多战报")
        for index, widget in enumerate((self.status_filter, self.scene_filter, self.refresh_button, self.more_button)):
            filters.addWidget(widget, index // 2, index % 2)
        root.addLayout(filters)
        self.status = QLabel("正在读取并检查战报…")
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.list = QTreeWidget()
        self.list.setAccessibleName("队伍战报匹配范围列表")
        self.list.setColumnCount(2)
        self.list.setHeaderLabels(("战报范围", "核验状态"))
        self.list.setRootIsDecorated(False)
        self.list.setWordWrap(True)
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        root.addWidget(self.list, 1)
        self.details = QLabel("选择战报，查看阵容与差异。")
        self.details.setTextFormat(Qt.PlainText)
        self.details.setWordWrap(True)
        root.addWidget(self.details)
        manual = QGroupBox()
        form = QFormLayout(manual)
        self.manual_report, self.manual_scope = QComboBox(), QComboBox()
        # 长战报标题仅影响下拉内容，不提升窗口最小宽度；四个筛选器共享可压缩的展示口径。
        for combo in (self.status_filter, self.scene_filter, self.manual_report, self.manual_scope):
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.manual_report.setAccessibleName("手工参考战报")
        self.manual_scope.setAccessibleName("手工战报范围")
        self.manual_report.addItem("请选择已读取的战报", None)
        form.addRow("战报", self.manual_report)
        form.addRow("范围", self.manual_scope)
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for control, label in ((self.start, "原轴开始秒"), (self.end, "原轴结束秒")):
            control.setRange(0, 86400)
            control.setDecimals(6)
            control.setSuffix(" 秒")
            control.setAccessibleName(label)
            # 数值精度保留；单列放置避免两份完整时间输入的最小宽度相加撑出小屏。
            form.addRow(label, control)
        self.manual_check = QPushButton("核验此范围")
        form.addRow(self.manual_check)
        # 手动配置独立滚动；展开不能把列表及底部确认键挤出小屏。
        self.manual_scroll = QScrollArea()
        self.manual_scroll.setWidgetResizable(True)
        self.manual_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.manual_scroll.setFrameShape(QScrollArea.NoFrame)
        self.manual_scroll.setMinimumHeight(100)
        self.manual_scroll.setMaximumHeight(190)
        self.manual_scroll.setWidget(manual)
        self.manual_section = Disclosure("手动选择时段", self.manual_scroll, parent=self)
        root.addWidget(self.manual_section)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("使用此战报")
        self.buttons.button(QDialogButtonBox.Ok).setToolTip("确认所选范围，不会立即计算")
        self.buttons.button(QDialogButtonBox.Cancel).setText("关闭")
        root.addWidget(self.buttons)
        self.buttons.accepted.connect(self._choose)
        self.buttons.rejected.connect(self.reject)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        self.status_filter.currentIndexChanged.connect(self._render)
        self.scene_filter.currentIndexChanged.connect(self._render)
        self.refresh_button.clicked.connect(self.refresh)
        self.more_button.clicked.connect(self._discover)
        self.manual_report.currentIndexChanged.connect(self._manual_changed)
        self.manual_scope.currentIndexChanged.connect(self._scope_changed)
        self.manual_check.clicked.connect(self._check_manual)
        self._selection_changed()
        self._scope_changed()
        fit_dialog_to_available_screen(self, QSize(940, 760))
        QTimer.singleShot(0, self.refresh)

    def refresh(self):
        if self._closing_result is not None:
            return
        self._next_offset = 0
        self._discover()

    def _discover(self):
        if self._closing_result is not None:
            return
        self._mode = "discover"
        offset, initial = self._next_offset, self._initial_range
        self.status.setText("正在更新战报列表，确认选择时会再次检查。")
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
                self.manual_report.addItem("请选择已读取的战报", None)
                self.scene_filter.clear()
                self.scene_filter.addItem("全部场景", None)
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
                    self.manual_report.addItem(candidate_label(candidate), entry.battle_record_id)
                scene = entry.environment_name or "场景未记录"
                if self.scene_filter.findData(scene) < 0:
                    self.scene_filter.addItem(scene, scene)
            if offset == 0 and self._initial_range is not None:
                scope = self._initial_range
                self.manual_report.setCurrentIndex(self.manual_report.findData(scope.record_id))
                self.manual_scope.setCurrentIndex(self.manual_scope.findData(scope.scope))
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
                self.status.setText(result.reason or STATUS_LABELS[result.status])
        else:
            self._render()

    def _progress(self, result):
        self._results[result.candidate.range] = result
        self._render()

    def _failed(self, message):
        self._warning = "读取／核验失败，已展示结果可能过期：" + message + "。请重试。"
        self.status.setText(self._warning)
        self.more_button.setEnabled(self._next_offset is not None)

    def _render(self, *_):
        selected = self.list.currentItem()
        key = selected.data(0, Qt.UserRole) if selected else None
        self.list.clear()
        matched = sum(result.status == MatchStatus.MATCHED for result in self._results.values())
        confirmed = sum(result.status in {MatchStatus.MATCHED, MatchStatus.DIFFERENT} for result in self._results.values())
        unknown = len(self._results) - confirmed
        self.counts_changed.emit((matched if confirmed else None, len(self._results)))
        amount = f"已确认 {matched} 个匹配" if confirmed else "尚未确认匹配数量"
        self.status.setText(f"已读取 {len(self._results)} 个范围 · {amount} · {unknown} 个未确认。"
                            "" + self._warning)
        for result in sorted(self._results.values(), key=lambda row: row.status != MatchStatus.MATCHED):
            mode, scene = self.status_filter.currentData(), self.scene_filter.currentData()
            related = bool(set(result.candidate.entry.character_ids) & set(self.team.character_ids))
            visible = (mode == "all" or mode == "matched" and result.status == MatchStatus.MATCHED
                       or mode == "related" and result.status != MatchStatus.MATCHED and related
                       or mode == "unknown" and result.status in {MatchStatus.PENDING, MatchStatus.INSUFFICIENT}
                       or mode == "conflict" and result.status == MatchStatus.CONFLICT
                       or mode == "error" and result.status in {MatchStatus.ERROR, MatchStatus.UNAVAILABLE})
            if not visible or scene is not None and scene != (result.candidate.entry.environment_name or "场景未记录"):
                continue
            label = STATUS_LABELS[result.status] + (" · 出伤成员相关" if mode == "related" else "")
            item = QTreeWidgetItem([candidate_label(result.candidate), label])
            item.setData(0, Qt.UserRole, result.candidate.range)
            item.setToolTip(0, candidate_label(result.candidate))
            item.setToolTip(1, result.reason)
            self.list.addTopLevelItem(item)
            if result.candidate.range == key:
                self.list.setCurrentItem(item)
        self.more_button.setEnabled(self._next_offset is not None and not self._controller.is_running())
        self._selection_changed()

    def _selection_changed(self):
        item = self.list.currentItem()
        result = self._results.get(item.data(0, Qt.UserRole)) if item else None
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(result is not None and result.status == MatchStatus.MATCHED)
        if result:
            missing = set(self.team.character_ids) - set(result.report_character_ids)
            extra = set(result.report_character_ids) - set(self.team.character_ids)
            names = lambda ids: "、".join(self.names.get(key, f"角色 {key}") for key in sorted(ids)) or "无"
            self.details.setText(STATUS_LABELS[result.status] + "\n" + result.reason
                                 + f"\n本队未确认成员：{names(missing)}；战报其他成员：{names(extra)}")
        else:
            self.details.setText("选择战报，查看阵容与差异。")

    def _choose(self):
        item = self.list.currentItem()
        result = self._results.get(item.data(0, Qt.UserRole)) if item else None
        if result is None or result.status != MatchStatus.MATCHED:
            return
        self._mode = "choose"
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)
        self._controller.submit(lambda cancelled, publish: self.service.match_page(
            self.team, (result.candidate,), cancelled=cancelled))

    def _manual_changed(self):
        record = self.manual_report.currentData()
        entry = next((item.entry for item in self._candidates if item.entry.battle_record_id == record), None)
        self.manual_scope.clear()
        if entry:
            for label, key, enabled in (("整场", "all", not entry.has_first_half and not entry.has_second_half),
                                        ("上半场", "first", entry.has_first_half), ("下半场", "second", entry.has_second_half),
                                        ("指定时段", "range", True)):
                if enabled:
                    self.manual_scope.addItem(label, key)

    def _scope_changed(self):
        for widget in (self.start, self.end):
            widget.setEnabled(self.manual_scope.currentData() == "range")

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
            self.status.setText("正在完成选择…" if result == QDialog.Accepted else "正在取消匹配，等待本次后台读取结束…")
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

    def is_running(self):
        return self._controller.is_running()
