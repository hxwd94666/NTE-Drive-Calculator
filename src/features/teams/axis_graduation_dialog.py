# 队伍轴伤结果窗口只消费worker展示投影；历史读取不求解，显式保存/选用/评价才提交对应任务。
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGridLayout, QLabel, QPlainTextEdit, QPushButton,
    QFormLayout, QScrollArea, QSizePolicy, QSpinBox, QTabWidget, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.report_matching import ReportRange
from src.domain.teams.axis_graduation_presentation import target_description
from src.features.teams.axis_graduation_controller import AxisGraduationSelection
from src.features.teams.axis_graduation_target_dialog import GraduationTargetDialog
from src.features.teams.widgets import Disclosure, ResponsiveGrid


STATE_TEXT = {
    "unselected": "尚未选择队伍", "no_benchmark": "先建立基准，或在历史记录中选用",
    "queued": "任务已排队", "running_history": "正在读取历史记录…",
    "running_configure": "正在读取目标资料…", "configuration_ready": "目标编辑资料已读取",
    "running_save": "正在复核来源并保存", "running_restore": "正在选用历史评价",
    "running_benchmark": "正在建立理论基准", "running_current": "正在计算当前毕业率…",
    "running_advice": "正在计算养成建议…", "running_equipment_advice": "正在计算装备与词条建议…", "history_ready": "历史已加载；当前配装可能已变化",
    "saved_math": "轴伤计算结果已保存",
    "historical_math": "已选用历史基准，可更新当前毕业率",
    "mathematical_preview": "固定轴预览；实战可执行性未证实",
    "search_limited_math": "搜索未完成，当前基准不代表全局最优",
    "outside_target": "当前养成/家具超出目标条件", "model_incomplete": "模型有缺项，保留未知",
    "current_stale": "配装已变化，请更新当前毕业率", "component_pending": "分析组件待更新",
    "running_validity": "正在检查结果是否过期…",
    "benchmark_stale": "基准需重建，旧结果仅供历史查看",
    "validity_unverified": "部分上下文尚未核实，旧结果仅供历史查看",
    "context_matching": "来源检查通过，未重算；实战可执行性仍未证实",
    "failed": "本次操作未完成，旧结果保留", "cancelled": "本次任务已取消，旧结果保留",
    "closing": "正在收尾", "closed": "已关闭",
}


class TeamAxisGraduationDialog(QDialog):
    axis_requested = Signal()

    def __init__(self, *, owner, team, parent=None):
        super().__init__(parent)
        self.owner, self.team = owner, team
        owner.set_team(team)
        if owner.can_check_validity and not owner.is_running():
            owner.mark_unverified()
        selected = owner.axis_selection
        self._axis = selected.axis if selected else None
        self._draft_target = selected.target if selected else None
        self._draft_rules = selected.rules if selected else None
        self._target_dialog = None
        self._axis_picker = None
        self._closing_result = None
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._fit)
        self.setWindowTitle("队伍毕业率 · " + team.name)
        self.setModal(True)
        outer = QVBoxLayout(self)
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName("毕业结果滚动区域")
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        hint = QLabel("选战报 → 设目标 → 建立 100% 基准。毕业率按固定动作与时点计算，实战可执行性未证实。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.choose_axis = self._button("1. 选择战报轴", self.axis_requested.emit)
        self.configure = self._button("2. 设置目标", owner.configure_target)
        self.benchmark = self._button("3. 建立基准", self._run_benchmark)
        self.benchmark.setObjectName("btnNew")
        self.benchmark.setToolTip("按所选目标搜索理论伤害作为 100% 标尺，不自动保存；可重复搜索")
        root.addWidget(ResponsiveGrid((self.choose_axis, self.configure, self.benchmark), minimum=155))
        advanced = QWidget()
        budget = QFormLayout(advanced)
        self.evaluation_budget, self.millis_budget = QSpinBox(), QSpinBox()
        self.evaluation_budget.setRange(1, 100000)
        self.evaluation_budget.setValue(256)
        self.evaluation_budget.setAccessibleName("理论搜索评价次数上限")
        self.millis_budget.setRange(1, 600000)
        self.millis_budget.setValue(15000)
        self.millis_budget.setSuffix(" 毫秒")
        self.millis_budget.setAccessibleName("理论搜索毫秒预算")
        budget.addRow("最多尝试次数", self.evaluation_budget)
        budget.addRow("最长搜索时间", self.millis_budget)
        self.validity = self._button("检查结果是否过期", owner.check_validity)
        self.validity.setToolTip("只检查配装、目标和组件是否变化，不重算")
        budget.addRow(self.validity)
        self.advanced = Disclosure("高级设置", advanced)
        root.addWidget(self.advanced)
        self.headline = QLabel("轴伤毕业：尚未读取历史")
        self.headline.setStyleSheet("font-size:17px;font-weight:700;")
        self.headline.setWordWrap(True)
        self.headline.setTextFormat(Qt.PlainText)
        root.addWidget(self.headline)
        self.damage, self.scope = QLabel(), QLabel()
        for label in (self.damage, self.scope):
            label.setWordWrap(True)
            label.setTextFormat(Qt.PlainText)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            root.addWidget(label)
        history = QGridLayout()
        self.benchmarks, self.evaluations = QComboBox(), QComboBox()
        for combo, name in ((self.benchmarks, "历史基准"), (self.evaluations, "历史评价")):
            combo.setAccessibleName(name)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        history.addWidget(QLabel("历史基准"), 0, 0)
        history.addWidget(self.benchmarks, 0, 1)
        history.addWidget(QLabel("历史评价"), 1, 0)
        history.addWidget(self.evaluations, 1, 1)
        self.bprev = self._button("基准上页", lambda: self._page("benchmark", -10))
        self.bnext = self._button("基准下页", lambda: self._page("benchmark", 10))
        self.eprev = self._button("评价上页", lambda: self._page("evaluation", -10))
        self.enext = self._button("评价下页", lambda: self._page("evaluation", 10))
        self.tabs = QTabWidget()
        self.tabs.setMinimumHeight(240)
        self.evidence, self.target, self.advice = QPlainTextEdit(), QPlainTextEdit(), QPlainTextEdit()
        self.recommend = self._button("养成建议", owner.load_advice)
        self.equipment_recommend = self._button("装备与词条建议", owner.load_equipment_advice)
        for button in (self.recommend, self.equipment_recommend):
            button.setToolTip("先保存基准或选用历史；只计算建议，不修改角色或装配")
        for title, edit in (("计算说明", self.evidence), ("毕业目标", self.target), ("提升建议", self.advice)):
            edit.setReadOnly(True)
            edit.setAccessibleName(title)
            container = QWidget()
            layout = QVBoxLayout(container)
            if edit is self.advice:
                layout.addWidget(ResponsiveGrid((self.recommend, self.equipment_recommend), columns=2, minimum=160))
            layout.addWidget(edit)
            self.tabs.addTab(container, title)
        self.history_tab = QWidget()
        history_layout = QVBoxLayout(self.history_tab)
        history_hint = QLabel("选用记录可恢复当时的战报轴和目标，不会重新计算。旧结果须检查后再用于当前配装。")
        history_hint.setWordWrap(True)
        history_layout.addWidget(history_hint)
        history_layout.addLayout(history)
        history_layout.addWidget(ResponsiveGrid((self.bprev, self.bnext, self.eprev, self.enext), columns=2, minimum=160))
        self.reload = self._button("刷新记录", lambda: owner.load_history())
        self.restore = self._button("选用此记录", self._restore)
        history_layout.addWidget(ResponsiveGrid((self.reload, self.restore), columns=2, minimum=140))
        history_layout.addStretch()
        self.tabs.addTab(self.history_tab, "历史记录")
        self.save = self._button("保存结果", lambda: owner.save_preview())
        self.current = self._button("更新当前毕业率", owner.update_current)
        self.current.setToolTip("沿用当前基准，重新计算当前配装的轴伤")
        self.stop = self._button("停止计算", owner.cancel)
        root.addWidget(ResponsiveGrid((self.current, self.save, self.stop), minimum=145))
        root.addWidget(self.tabs, 1)
        close = self._button("关闭", self.reject)
        outer.addWidget(close)
        self.benchmarks.currentIndexChanged.connect(self._benchmark_changed)
        self.evaluations.currentIndexChanged.connect(self._sync)
        owner.state_changed.connect(self._state_changed)
        owner.history_ready.connect(self._history)
        owner.display_ready.connect(self._display)
        owner.configuration_ready.connect(self._configuration)
        owner.error.connect(self._error)
        owner.drained.connect(self._sync)
        self.finished.connect(self._finished)
        if owner.display is not None:
            self._display(owner.display)
        self._state_changed(owner.state)
        owner.load_history()

    def _button(self, text, callback):
        button = QPushButton(text)
        button.setAccessibleName(text)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        return button

    def _state_changed(self, state):
        self.status.setText(STATE_TEXT.get(state, "任务状态待确认")
                            + ("；原预览已过期" if self.owner.current_stale else ""))
        self._sync()

    def _error(self, message):
        self.status.setText("本次操作未完成：" + message + "。旧结果保留，请检查后重试。")

    def _sync(self, *_args):
        idle = not self.owner.is_running() and self._closing_result is None
        view = self.owner.history
        self.reload.setEnabled(idle)
        self.choose_axis.setEnabled(idle)
        self.configure.setEnabled(idle)
        self.benchmark.setEnabled(idle and self._axis is not None and self._draft_target is not None)
        self.evaluation_budget.setEnabled(idle)
        self.millis_budget.setEnabled(idle)
        self.restore.setEnabled(idle and self.evaluations.currentData() is not None)
        self.save.setEnabled(self.owner.can_save)
        self.current.setEnabled(idle and self.owner.can_update)
        self.validity.setEnabled(idle and self.owner.can_check_validity)
        self.recommend.setEnabled(idle and self.owner.can_update)
        self.equipment_recommend.setEnabled(idle and self.owner.can_load_equipment_advice)
        self.stop.setEnabled(self.owner.is_running())
        self.benchmarks.setEnabled(idle)
        self.evaluations.setEnabled(idle)
        for button, enabled in ((self.bprev, view and view.benchmark_offset > 0), (self.bnext, view and view.more_benchmarks),
                                (self.eprev, view and view.evaluation_offset > 0), (self.enext, view and view.more_evaluations)):
            button.setEnabled(bool(idle and enabled))

    def _history(self, view):
        if view.team_id != self.team.team_id:
            return
        for combo, entries, selected in ((self.benchmarks, view.benchmarks, view.benchmark_id),
                                          (self.evaluations, view.evaluations, view.selected_evaluation_id)):
            combo.blockSignals(True)
            combo.clear()
            for key, text in entries:
                combo.addItem(text + " · " + key[-8:], key)
            index = combo.findData(selected)
            combo.setCurrentIndex(index if index >= 0 else 0 if combo is self.evaluations and entries else -1)
            combo.blockSignals(False)
        if self.owner.display is None and view.display is not None:
            self._display(view.display)
        elif self.owner.display is None:
            self.headline.setText("轴伤毕业：尚未选用已保存基准")
        self._sync()

    def _display(self, display):
        selected = self.owner.axis_selection
        if selected is not None:
            self._axis, self._draft_target, self._draft_rules = selected.axis, selected.target, selected.rules
        self.headline.setText(display.headline)
        self.damage.setText(display.damage)
        self.scope.setText(display.scope)
        self.evidence.setPlainText(display.evidence)
        self.target.setPlainText(display.target)
        self.advice.setPlainText(display.advice)

    @property
    def reference_axis(self):
        return self._axis

    def set_reference_axis(self, axis):
        if not isinstance(axis, ReportRange) or self.owner.team != self.team:
            raise ValueError("参考轴需要本次队伍明确匹配的范围")
        self._axis = axis
        self._apply_draft()

    def attach_axis_picker(self, picker):
        self._axis_picker = picker
        picker.finished.connect(self._axis_picker_finished)

    def _axis_picker_finished(self, _result):
        self._axis_picker = None
        if self._closing_result is not None:
            result, self._closing_result = self._closing_result, None
            self.done(result)

    def _configuration(self, configuration):
        if configuration.team != self.team or self.owner.configuration is not configuration:
            return
        if self._target_dialog is not None:
            self._target_dialog.raise_()
            return
        dialog = GraduationTargetDialog(configuration=configuration, parent=self)
        self._target_dialog = dialog
        def finished(result):
            self._target_dialog = None
            if (result == QDialog.Accepted and dialog.target is not None and self.owner.team == self.team
                    and self.owner.configuration is configuration):
                self._draft_target, self._draft_rules = dialog.target, configuration.rules
                self._apply_draft()
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def _apply_draft(self):
        if self._draft_target is not None:
            self.target.setPlainText(target_description(self._draft_target.to_payload()))
        self.scope.setText("参考轴：" + (self._axis.label if self._axis else "尚未选择"))
        if self._axis is not None and self._draft_target is not None:
            self.owner.set_selection(AxisGraduationSelection(self.team, self._axis, self._draft_target, self._draft_rules))
            self.headline.setText("战报轴和目标已就绪，点击“建立基准”开始计算")
            self.damage.clear()
            self.evidence.clear()
            self.advice.clear()
        self._sync()

    def _run_benchmark(self):
        if self._axis is not None and self._draft_target is not None:
            self.owner.run_target_benchmark(max_evaluations=self.evaluation_budget.value(),
                                            max_millis=self.millis_budget.value())

    def _restore(self):
        key = self.evaluations.currentData()
        if key is not None:
            self.owner.restore_saved(key)

    def _benchmark_changed(self, _index):
        key = self.benchmarks.currentData()
        view = self.owner.history
        if key is not None and view is not None:
            self.owner.load_history(benchmark_id=key, benchmark_offset=view.benchmark_offset)

    def _page(self, kind, delta):
        view = self.owner.history
        if view is not None:
            options = dict(benchmark_id=view.benchmark_id, benchmark_offset=view.benchmark_offset,
                           evaluation_offset=view.evaluation_offset)
            options[kind + "_offset"] = max(0, options[kind + "_offset"] + delta)
            self.owner.load_history(**options)

    def _finished(self, _result):
        # 窗口可立即退场；线程由队伍owner持有并计入账号切换门禁，这里只撤销本次发布权。
        if self.owner.team == self.team:
            self.owner.cancel()
        if self._target_dialog is not None:
            self._target_dialog.reject()

    def done(self, result):
        # 轴匹配子窗有自己的读取worker；先让它物理收尾，不能随父窗口deleteLater销毁运行中的线程。
        if self._axis_picker is not None:
            if self._axis_picker.is_running():
                self._closing_result = result
                self.owner.cancel()
                self._axis_picker.reject()
                self._sync()
                return
            self._axis_picker.reject()
        super().done(result)

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self, QSize(760, 720))
        # Windows首次创建原生标题栏后会调整客户区坐标；下一轮事件中再次按可用屏幕收口。
        self._geometry_timer.start(0)

    def _fit(self):
        if self.isVisible():
            fit_dialog_to_available_screen(self, QSize(760, 720))
