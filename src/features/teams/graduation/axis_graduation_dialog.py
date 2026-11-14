# 队伍轴伤结果窗口只消费worker展示投影；历史读取不求解，显式保存/选用/评价才提交对应任务。
import json
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QLabel, QHBoxLayout,
    QFormLayout, QScrollArea, QSizePolicy, QSpinBox, QVBoxLayout, QWidget,
)

from src.features.teams.compact_details import CompactReadOnlyText, CurrentContentTabs, InitialContentFit, reveal_expanded_details
from src.domain.teams.report_matching import ReportRange
from src.features.teams.graduation.axis_graduation_presentation import target_description
from src.features.teams.graduation.axis_graduation_controller import AxisGraduationSelection
from src.features.teams.graduation.axis_graduation_target_dialog import GraduationTargetDialog
from src.features.teams.widgets import ResponsiveGrid
from src.features.teams.graduation.flow_text import FlowReadOnlyText
from src.features.teams.graduation.target_view import GraduationTargetView
from src.features.teams.dialog_support import ExplicitActionDialog, LocalizedDisclosure
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_tooltip, resolve_text_catalog


STATE_IDS = (
    'unselected',
    'no_benchmark',
    'queued',
    'running_history',
    'running_configure',
    'configuration_ready',
    'running_save',
    'running_restore',
    'running_benchmark',
    'running_current',
    'running_advice',
    'running_equipment_advice',
    'history_ready',
    'saved_math',
    'historical_math',
    'mathematical_preview',
    'search_limited_math',
    'outside_target',
    'model_incomplete',
    'current_stale',
    'component_pending',
    'running_validity',
    'benchmark_stale',
    'validity_unverified',
    'context_matching',
    'failed',
    'cancelled',
    'closing',
    'closed',
)


class TeamAxisGraduationDialog(ExplicitActionDialog):
    axis_requested = Signal()

    def __init__(self, *, owner, team, parent=None, catalog=None, roles=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
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
        self.setWindowTitle(self.text("teams.graduation.title", team=team.name))
        self.setModal(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 14)
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName(self.text('teams.graduation.scroll'))
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        hint = QLabel(self.text('teams.graduation.compact_hint'))
        self._result_hint = hint
        hint.setTextFormat(Qt.PlainText)
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.choose_axis = self._button(self.text('teams.graduation.choose_axis'), self.axis_requested.emit)
        self.configure = self._button(self.text('teams.graduation.configure'), owner.configure_target)
        self.benchmark = self._button(self.text('teams.graduation.benchmark'), self._run_benchmark)
        self.benchmark.setObjectName("btnNew")
        self.benchmark.setToolTip(plain_tooltip(self.text('teams.graduation.benchmark_hint')))
        root.addWidget(ResponsiveGrid((self.choose_axis, self.configure, self.benchmark), minimum=155))
        advanced = QWidget()
        budget = QFormLayout(advanced)
        self.evaluation_budget, self.millis_budget = QSpinBox(), QSpinBox()
        self.evaluation_budget.setRange(1, 100000)
        self.evaluation_budget.setValue(256)
        self.evaluation_budget.setAccessibleName(self.text('teams.graduation.budget_count_accessible'))
        self.millis_budget.setRange(1, 600000)
        self.millis_budget.setValue(15000)
        self.millis_budget.setSuffix(self.text('teams.graduation.milliseconds'))
        self.millis_budget.setAccessibleName(self.text('teams.graduation.budget_time_accessible'))
        budget.addRow(self.text('teams.graduation.budget_count'), self.evaluation_budget)
        budget.addRow(self.text('teams.graduation.budget_time'), self.millis_budget)
        self.validity = self._button(self.text('teams.graduation.validity'), owner.check_validity)
        self.validity.setToolTip(plain_tooltip(self.text('teams.graduation.validity_hint')))
        budget.addRow(self.validity)
        for row in range(budget.rowCount()):
            item = budget.itemAt(row, QFormLayout.LabelRole)
            if item is not None:
                label = item.widget()
                label.setTextFormat(Qt.PlainText)
                label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.advanced = LocalizedDisclosure(self.text('teams.graduation.advanced'), advanced, catalog=self.text_catalog)
        root.addWidget(self.advanced)
        self.headline = QLabel(self.text('teams.graduation.unread'))
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
        self.benchmarks, self.evaluations = QComboBox(), QComboBox()
        for combo, name in ((self.benchmarks, self.text('teams.graduation.benchmarks')),
                            (self.evaluations, self.text('teams.graduation.evaluations'))):
            combo.setAccessibleName(name)
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(8)
            combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.bprev = self._button(self.text('teams.graduation.history_previous'), lambda: self._page("benchmark", -10))
        self.bnext = self._button(self.text('teams.graduation.history_next'), lambda: self._page("benchmark", 10))
        self.eprev = self._button(self.text('teams.graduation.history_previous'), lambda: self._page("evaluation", -10))
        self.enext = self._button(self.text('teams.graduation.history_next'), lambda: self._page("evaluation", 10))
        for name in ('bprev', 'bnext', 'eprev', 'enext'):
            button = getattr(self, name)
            button.setAccessibleName(self.text('teams.graduation.' + name))
            button.setToolTip(plain_tooltip(self.text('teams.graduation.' + name)))
        self.tabs = CurrentContentTabs()
        self.evidence = CompactReadOnlyText()
        self.target = GraduationTargetView(roles=roles, catalog=self.text_catalog)
        self.advice = FlowReadOnlyText()
        self.recommend = self._button(self.text('teams.graduation.cultivation_advice'), owner.load_advice)
        self.equipment_recommend = self._button(self.text('teams.graduation.equipment_advice'), owner.load_equipment_advice)
        for button in (self.recommend, self.equipment_recommend):
            button.setToolTip(plain_tooltip(self.text('teams.graduation.advice_hint')))
        for title, edit in ((self.text('teams.graduation.result_tab'), self.evidence), (self.text('teams.graduation.target'), self.target), (self.text('teams.graduation.advice'), self.advice)):
            edit.setAccessibleName(title)
            container = QWidget()
            layout = QVBoxLayout(container)
            if edit is self.advice:
                note = QLabel(self.text('teams.graduation.advice_note'))
                note.setTextFormat(Qt.PlainText)
                note.setWordWrap(True)
                layout.addWidget(note)
                layout.addWidget(ResponsiveGrid((self.recommend, self.equipment_recommend), columns=2, minimum=160))
            if edit is self.evidence:
                self.diagnostics_section = LocalizedDisclosure(self.text('teams.graduation.diagnostics'), edit,
                                                               catalog=self.text_catalog)
                layout.addWidget(self.diagnostics_section)
            else:
                layout.addWidget(edit)
            layout.setAlignment(Qt.AlignTop)
            self.tabs.addTab(container, title)
        self.history_tab = QWidget()
        history_layout = QVBoxLayout(self.history_tab)
        history_hint = QLabel(self.text('teams.graduation.history_compact_hint'))
        history_hint.setTextFormat(Qt.PlainText)
        history_hint.setWordWrap(True)
        history_layout.addWidget(history_hint)
        history_layout.addWidget(self._history_row(self.text('teams.graduation.history_benchmark'),
            self.benchmarks, self.bprev, self.bnext))
        history_layout.addWidget(self._history_row(self.text('teams.graduation.history_evaluation'),
            self.evaluations, self.eprev, self.enext))
        self.reload = self._button(self.text('teams.graduation.reload'), lambda: owner.load_history())
        self.restore = self._button(self.text('teams.graduation.restore'), self._restore)
        history_layout.addWidget(ResponsiveGrid((self.reload, self.restore), columns=2, minimum=140))
        history_layout.addStretch()
        self.tabs.addTab(self.history_tab, self.text('teams.graduation.history'))
        self.save = self._button(self.text('teams.graduation.save'), lambda: owner.save_preview())
        self.current = self._button(self.text('teams.graduation.current'), owner.update_current)
        self.current.setToolTip(plain_tooltip(self.text('teams.graduation.current_hint')))
        self.stop = self._button(self.text('teams.graduation.stop'), owner.cancel)

        root.addWidget(self.tabs)
        self.footer = QWidget()
        footer = QHBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch()
        actions = (self.stop, self.current, self.save)
        for button in actions:
            button.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
            footer.addWidget(button)
        for first, second in zip(actions, actions[1:]):
            QWidget.setTabOrder(first, second)
        outer.addWidget(self.footer)
        self._initial_fit = InitialContentFit(self, body=content, footer=self.footer, width=720)
        self.ratio_label = QLabel(self.text('teams.graduation.metric_unknown'))
        self.ratio_label.setTextFormat(Qt.PlainText)
        self.ratio_label.setStyleSheet('font-size:28px;font-weight:700;')
        self.ratio_label.setAccessibleName(self.text('teams.graduation.metric_unknown'))
        self.ratio_label.setWordWrap(True)
        root.insertWidget(0, self.ratio_label)
        for index, widget in enumerate((self.headline, self.damage, self.scope, self.status), 1):
            root.removeWidget(widget)
            root.insertWidget(index, widget)
        self.headline.setStyleSheet('font-size:13px;font-weight:600;')
        root.removeWidget(self.advanced)
        self.tabs.widget(0).layout().addWidget(self.advanced)
        reveal_expanded_details(self.scroll, self.diagnostics_section)
        reveal_expanded_details(self.scroll, self.advanced)
        reveal_expanded_details(self.scroll, self.target.details_section)
        self.tabs.currentChanged.connect(self._page_changed)
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

    def _set_metric(self, text):
        self.ratio_label.setText(text)
        self.ratio_label.setAccessibleName(text)

    def _button(self, text, callback):
        button = TextActionButton(text)
        button.setAccessibleName(text)
        button.setAutoDefault(False)
        button.clicked.connect(callback)
        return button

    def _history_row(self, title, combo, previous, next_button):
        row = QWidget()
        row.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = ElidedTextLabel(title)
        label.setMaximumWidth(140)
        label.setBuddy(combo)
        layout.addWidget(label)
        layout.addWidget(combo, 1)
        layout.addWidget(previous)
        layout.addWidget(next_button)
        return row

    def _page_changed(self, index):
        # 阅读目标/建议/历史时只保留紧凑轴范围、比例与状态，完整结果仍在结果页。
        overview = index == 0
        for label in (self.headline, self.damage, self._result_hint):
            label.setVisible(overview)
        self.ratio_label.setStyleSheet(f'font-size:{28 if overview else 20}px;font-weight:700;')

    def _state_changed(self, state):
        self.status.setText((self.text("teams.graduation.state." + state) if state in STATE_IDS else self.text("teams.graduation.state_unknown"))
                            + (self.text('teams.graduation.stale') if self.owner.current_stale else ""))
        self._sync()

    def _error(self, message):
        self.status.setText(self.text('teams.graduation.failed_prefix') + message + self.text('teams.graduation.failed_suffix'))
        self._initial_fit.ready()

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
            self._set_metric(self.text("teams.graduation.metric_pending"))
            self.headline.setText(self.text('teams.graduation.no_selection'))
        self._sync()
        self._initial_fit.ready()

    def _display(self, display):
        selected = self.owner.axis_selection
        if selected is not None:
            self._axis, self._draft_target, self._draft_rules = selected.axis, selected.target, selected.rules
        ratio = display.ratio_percent
        self._set_metric(self.text('teams.graduation.metric', value=f'{ratio:.2f}') if ratio is not None
                            else self.text('teams.graduation.metric_unknown'))
        classification = self.text('teams.graduation.metric_history' if display.historical else 'teams.graduation.metric_current')
        self.ratio_label.setAccessibleDescription(classification + '\n' + display.headline)
        self.ratio_label.setToolTip(plain_tooltip(classification + '\n' + display.headline))
        self.headline.setText(display.headline)
        self.damage.setText(display.damage)
        self.scope.setText(display.scope)
        self.evidence.setPlainText(display.evidence)
        payload = json.loads(display.target_json) if display.target_json else None
        description = display.target
        if payload is None and not description and selected is not None:
            payload = selected.target.to_payload()
            description = target_description(payload, catalog=self.text_catalog)
        self.target.set_target(payload, description)
        self.advice.setText(display.advice)

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
        dialog = GraduationTargetDialog(configuration=configuration, parent=self, catalog=self.text_catalog)
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
            payload = self._draft_target.to_payload()
            self.target.set_target(payload, target_description(payload, catalog=self.text_catalog))
        self.scope.setText(self.text('teams.graduation.axis_prefix') + (self._axis.label if self._axis else self.text('teams.graduation.axis_empty')))
        if self._axis is not None and self._draft_target is not None:
            self.owner.set_selection(AxisGraduationSelection(self.team, self._axis, self._draft_target, self._draft_rules))
            self.headline.setText(self.text('teams.graduation.draft_ready'))
            self._set_metric(self.text("teams.graduation.metric_pending"))
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
        self._initial_fit.shown()
