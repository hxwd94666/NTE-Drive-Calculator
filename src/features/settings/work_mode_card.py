# 构建统一工作模式入口、逐功能检查结果与必要处理操作。
from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog, set_button_text
from src.ui.common.enum_text import ui_enum_text
from datetime import datetime

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout,
    QPushButton, QProgressBar, QScrollArea, QVBoxLayout,
)
from src.app.theme import theme_color
from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.version import __version__
from src.ui.common.widgets import NoWheelComboBox

MODE_LABELS = {"offline": "离线", "low": "低风险", "medium": "中风险", "developer": "开发"}
MODE_DESCRIPTIONS = {
    "offline": "本地计算、配装、已保存数据与历史战报分析。",
    "low": "以上功能 + 基础同步/抓包 + 鼠标或手柄扫描；不使用游戏组件。",
    "medium": "以上功能 + 原生同步、原生战报、极速装配、锁定/弃置及插件。",
    "developer": "抓包与原生双线对比，仅供开发人员使用。",
}
MODE_CONFIRMATIONS = {
    "offline": {
        "warning_title": "切换后将断开游戏连接",
        "warning_detail": "停止采集与同步，并关闭两个插件开关。",
        "available": "本地计算、配装、已保存数据、历史战报分析",
        "unavailable": "数据同步、战报采集、游戏操作、插件",
        "after": "停止后台连接；游戏退出后清理已部署组件。",
        "note": "离线模式不会连接游戏。确认后会保存此模式。",
    },
    "low": {
        "warning_title": "此模式存在风险",
        "warning_detail": "抓包与模拟输入可能触发游戏保护或兼容问题。",
        "available": "基础同步/抓包、鼠标或手柄扫描",
        "unavailable": "角色状态同步、原生同步、原生战报、极速装配、插件",
        "after": "停止原生连接；游戏退出后清理已部署组件。",
        "note": "自动同步仍由工作台开关控制。确认后会保存此模式，后续版本更新继续沿用。",
    },
    "medium": {
        "warning_title": "此模式存在风险",
        "warning_detail": "加载游戏组件并执行游戏内操作，可能触发游戏保护或兼容问题。",
        "available": "原生同步、原生战报、极速装配、锁定/弃置、插件",
        "unavailable": "基础同步/抓包、双线战报对比",
        "after": "开启同步并确认准备后，游戏关闭时部署或更新所需组件。",
        "note": "确认模式不等于开启自动同步；工作台首次开启会先显示环境检测。模式选择会保存。",
    },
    "developer": {
        "warning_title": "仅供开发人员使用",
        "warning_detail": "抓包与原生双线同时运行，可能触发游戏保护或兼容问题。",
        "available": "中风险全部功能、抓包与原生双线战报对比",
        "unavailable": "不建议用于日常使用",
        "after": "开启同步并确认准备后，游戏关闭时部署或更新所需组件。",
        "note": "确认模式不等于开启自动同步；工作台首次开启会先显示环境检测。模式选择会保存。",
    },
}
STATE_LABELS = {
    "available": "可用", "waiting": "正常等待", "waiting_login": "等待登录",
    "warning": "警告", "missing": "缺少条件", "fault": "故障",
    "cleanup_pending": "清理待完成",
}
ISSUE_STATES = frozenset({"fault", "missing", "cleanup_pending"})
WARNING_STATES = frozenset({"waiting_login", "warning"})


def _check_state_label(item, *, text_catalog=None) -> str:
    if dict(item.facts).get("inspection_incomplete") is True:
        return resolve_text_catalog(text_catalog).render("ui.settings.fact.incomplete")
    return ui_enum_text(text_catalog, STATE_LABELS)[item.state.value]


def _fact_value(value, *, text_catalog=None) -> str:
    if value is True:
        return resolve_text_catalog(text_catalog).render("common.yes")
    if value is False:
        return resolve_text_catalog(text_catalog).render("common.no")
    if value is None:
        return resolve_text_catalog(text_catalog).render("ui.settings.fact.unknown")
    return str(value)


def report_text(report, *, text_catalog=None) -> str:
    rows = []
    # 故障优先，其余同组顺序保持；折叠卡片不替代完整可复制证据。
    for item in sorted(report.features, key=lambda item: item.state.value != "fault"):
        row = f"{item.label}：{_check_state_label(item, text_catalog=text_catalog)}\n{item.detail}"
        facts = dict(item.facts)
        labels = (
            ("game_running", "游戏进程"), ("core_available", "配套 Core"),
            ("npcap", "Npcap"), ("listening", "抓包监听"),
            ("files", "文件核对"), ("pipe", "管道"), ("handshake", "握手"),
            ("supported", "能力声明"), ("snapshot", "快照"), ("ready", "业务就绪"),
            ("complete", "完整性"), ("source_coverage", "来源覆盖"),
            ("projection_complete", "字段投影完整"),
            ("profile_projection_supported", "角色字段投影能力"),
        )
        values = [f"{ui_enum_text(text_catalog, label)}：{_fact_value(facts[key], text_catalog=text_catalog)}" for key, label in labels if key in facts]
        if values:
            row += "\n" + resolve_text_catalog(text_catalog).render("ui.settings.fact.heading") + " · ".join(values)
        rows.append(row)
    return "\n\n".join(rows)


def report_summary(report, *, text_catalog=None) -> str:
    counts = {}
    for item in report.features:
        # 语言补丁允许两个标签同名；分组仍以状态及检测完整性身份决定。
        key = "inspection_incomplete" if dict(item.facts).get("inspection_incomplete") is True else item.state.value
        previous, count = counts.get(key, (item, 0))
        counts[key] = previous, count + 1
    return " · ".join(
        resolve_text_catalog(text_catalog).render('ui.settings.report_summary.count',
            state=_check_state_label(item, text_catalog=text_catalog), count=count)
        for item, count in counts.values()
    ) or resolve_text_catalog(text_catalog).render('ui.settings.report_summary.empty')


def _report_groups(report, *, text_catalog=None):
    """按状态与共同原因归组显示；逐功能事实仍保留在完整检测文本中。"""
    sections = ([], [], [], [])
    for item in report.features:
        section = 0 if item.state.value in ISSUE_STATES else (
            1 if item.state.value in WARNING_STATES else
            2 if item.state.value == "waiting" else 3
        )
        key = (item.state.value, _check_state_label(item, text_catalog=text_catalog), item.detail)
        group = next((group for group in sections[section] if group[0] == key), None)
        if group is None:
            group = (key, [])
            sections[section].append(group)
        group[1].append(item.label)
    sections[0].sort(key=lambda group: group[0][0] != "fault")
    return sections


def prompt_offline_sync_mode(parent, *, text_catalog=None) -> bool:
    """离线请求同步只询问是否前往设置，不提升模式或续跑同步。"""
    dialog = QDialog(parent)
    dialog.setObjectName("offlineSyncModeDialog")
    dialog.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.settings.prompt_offline_sync_mode.dialog'))
    dialog.setWindowModality(Qt.WindowModal)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 16)
    layout.setSpacing(18)
    guidance = plain_label(
        resolve_text_catalog(text_catalog).render('ui.settings.prompt_offline_sync_mode.guidance'),
        dialog,
    )
    guidance.setObjectName("offlineSyncModeGuidance")
    guidance.setTextFormat(Qt.PlainText)
    guidance.setWordWrap(True)
    guidance.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
    layout.addWidget(guidance)
    actions = QHBoxLayout()
    actions.addStretch()
    settings_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.prompt_offline_sync_mode.settings_button'), dialog)
    settings_button.clicked.connect(dialog.accept)
    actions.addWidget(settings_button)
    cancel_button = TextActionButton(resolve_text_catalog(text_catalog).render('common.cancel'), dialog)
    cancel_button.setDefault(True)
    cancel_button.setFocus()
    cancel_button.clicked.connect(dialog.reject)
    actions.addWidget(cancel_button)
    layout.addLayout(actions)
    fit_dialog_to_available_screen(dialog, QSize(520, 180))
    return dialog.exec() == QDialog.Accepted


class ModeReportDialog(QDialog):
    """展示本次显式检测与终态；预检关闭和已确认组件处理分别解释。"""

    def __init__(self, parent, controller, *, sync_action_provider=None, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._controller = controller
        self._sync_action_provider = sync_action_provider
        self._settings_target = "deployment"
        self._preview = False
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        header = QHBoxLayout()
        self.overview = plain_label(resolve_text_catalog(text_catalog).render('ui.settings.init.overview'), self)
        self.overview.setObjectName("modeReportOverview")
        self.overview.setWordWrap(True)
        self.overview.setStyleSheet("font-size:16px;font-weight:700")
        header.addWidget(self.overview, 1)
        self.copy_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.copy_button'), self)
        self.copy_button.setObjectName("modeReportCopy")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_result)
        header.addWidget(self.copy_button)
        layout.addLayout(header)
        self.metadata = plain_label("", self)
        self.metadata.setObjectName("modeReportMetadata")
        self.metadata.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:11px")
        layout.addWidget(self.metadata)
        self.status_hint = plain_label(
            resolve_text_catalog(text_catalog).render('ui.settings.init.status_hint'), self,
        )
        self.status_hint.setObjectName("modeReportStatusHint")
        self.status_hint.setWordWrap(True)
        self.status_hint.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
        self.status_hint.hide()
        layout.addWidget(self.status_hint)
        self.label = plain_label()
        self.label.setTextFormat(Qt.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignTop)
        self.label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        self._copy_text = ""
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.preflight_summary = plain_label()
        self.preflight_summary.setWordWrap(True)
        self.preflight_summary.setObjectName("syncPreflightSummary")
        self.preflight_summary.setStyleSheet(
            f"background:{theme_color('#161b22')};border:1px solid {theme_color('#30363d')};"
            "border-radius:6px;padding:10px"
        )
        self.preflight_summary.hide()
        layout.addWidget(self.preflight_summary)
        layout.addWidget(self.progress)
        scroll = QScrollArea(self)
        scroll.setObjectName("modeReportScroll")
        scroll.setWidgetResizable(True)
        content = QFrame(scroll)
        content.setObjectName("modeReportContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(4, 4, 4, 4)
        content_layout.setSpacing(10)
        self.results = QFrame(content)
        self.results_layout = QVBoxLayout(self.results)
        self.results_layout.setContentsMargins(0, 0, 0, 0)
        self.results_layout.setSpacing(10)
        content_layout.addWidget(self.results)
        self.diagnostic_toggle = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.diagnostic_toggle'), content)
        self.diagnostic_toggle.setObjectName("modeReportDiagnosticsToggle")
        self.diagnostic_toggle.setCheckable(True)
        self.diagnostic_toggle.toggled.connect(self._toggle_diagnostics)
        content_layout.addWidget(self.diagnostic_toggle)
        self.label.hide()
        content_layout.addWidget(self.label)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        footer = QHBoxLayout()
        footer.addStretch()
        self.settings_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.settings_button'))
        self._primary_action = self._open_environment_settings
        self.settings_button.clicked.connect(lambda: self._primary_action())
        footer.addWidget(self.settings_button)
        self.close_button = TextActionButton(resolve_text_catalog(text_catalog).render('common.close'))
        self.close_button.setFixedWidth(72)
        self.close_button.clicked.connect(self.reject)
        self.retry_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.retry_button'))
        self.retry_button.setFixedWidth(88)
        self.retry_button.clicked.connect(self._retry)
        footer.addWidget(self.retry_button)
        footer.addWidget(self.close_button)
        self.footer = footer
        layout.addLayout(footer)
        self.actions = QVBoxLayout()
        self.actions.setSpacing(8)
        layout.addLayout(self.actions)
        fit_dialog_to_available_screen(self, QSize(760, 560))

    def _toggle_diagnostics(self, expanded):
        self.label.setVisible(expanded)
        self.diagnostic_toggle.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.toggle_diagnostics.diagnostic_toggle') if expanded else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.init.diagnostic_toggle')
        )

    def _clear_results(self):
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # 延迟销毁前保持父窗口，避免短暂生成无主题的顶层窗口。
                widget.hide()
                widget.deleteLater()

    def _add_result_section(self, title, groups, tone):
        if not groups:
            return
        heading = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.add_result_section.heading', title=f'{title}', value=f'{sum((len(labels) for _key, labels in groups))}'), self.results)
        heading.setStyleSheet(f"color:{theme_color(tone)};font-weight:700;font-size:13px")
        self.results_layout.addWidget(heading)
        for (state, state_label, detail), labels in groups:
            row = QFrame(self.results)
            row.setObjectName("modeReportFeatureRow")
            color = theme_color(
                "#f85149" if state in ISSUE_STATES else
                "#d29922" if state in WARNING_STATES else "#58a6ff"
            )
            row.setStyleSheet(
                f"QFrame#modeReportFeatureRow{{background:{theme_color('#161b22')};"
                f"border:1px solid {theme_color('#30363d')};border-left:3px solid {color};"
                "border-radius:6px}"
            )
            body = QVBoxLayout(row)
            body.setContentsMargins(12, 8, 12, 8)
            body.setSpacing(3)
            label = plain_label(f"{state_label}  ·  {'、'.join(labels)}", row)
            label.setWordWrap(True)
            label.setStyleSheet(f"color:{color};font-weight:700")
            body.addWidget(label)
            # 详细类型证据留在可复制的折叠报告，问题卡只展示决策所需说明。
            explanation = plain_label(str(detail).split("\n诊断：", 1)[0], row)
            explanation.setWordWrap(True)
            body.addWidget(explanation)
            self.results_layout.addWidget(row)

    def _render_report(self, report):
        self._clear_results()
        issues, warnings, waiting, available = _report_groups(report, text_catalog=getattr(self, 'text_catalog', None))
        problem_count = sum(len(labels) for _key, labels in issues)
        warning_count = sum(len(labels) for _key, labels in warnings)
        self.status_hint.setVisible(bool(issues or warnings or waiting))
        waiting_count = sum(len(labels) for _key, labels in waiting)
        ready_count = sum(len(labels) for _key, labels in available)
        if problem_count:
            self.overview.setText(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.overview', problem_count=f'{problem_count}', warning_count=f'{warning_count}', waiting_count=f'{waiting_count}', ready_count=f'{ready_count}')
            )
        elif warning_count:
            self.overview.setText(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.overview_2', warning_count=f'{warning_count}', waiting_count=f'{waiting_count}', ready_count=f'{ready_count}')
            )
        elif waiting_count:
            self.overview.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.overview_3', waiting_count=f'{waiting_count}', ready_count=f'{ready_count}'))
        else:
            self.overview.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.overview_4', ready_count=f'{ready_count}'))
        self._add_result_section(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.self'), issues, "#f85149")
        self._add_result_section(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.self_2'), warnings, "#d29922")
        self._add_result_section(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.self_3'), waiting, "#58a6ff")
        if available:
            heading = plain_label(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.heading', ready_count=f'{ready_count}'), self.results)
            heading.setStyleSheet(f"color:{theme_color('#3fb950')};font-weight:700;font-size:13px")
            self.results_layout.addWidget(heading)
            names = plain_label("、".join(label for _key, labels in available for label in labels), self.results)
            names.setWordWrap(True)
            self.results_layout.addWidget(names)

    def _open_environment_settings(self):
        controller, target = self._controller, self._settings_target
        self.accept()
        QTimer.singleShot(0, lambda: controller.open_settings(target))

    def _set_primary_action(self, title, callback):
        self.settings_button.setText(title)
        self._primary_action = callback
        self.settings_button.show()

    def _retry(self):
        if self._preview:
            self._controller.check(show=True, preview=True)
        else:
            self._controller.check(show=True, retry_deployment=True)

    def _clear_actions(self):
        while self.actions.count():
            button = self.actions.takeAt(0).widget()
            button.hide()
            button.deleteLater()

    def begin(self, mode, *, preview=False):
        self.setWindowTitle(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.begin.self', value=f'{MODE_LABELS[mode]}'))
        self._settings_target = "deployment"
        self._preview = preview
        self._clear_results()
        self.overview.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.begin.overview') if preview else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.begin.overview_2'))
        self.metadata.clear()
        self.status_hint.hide()
        self.diagnostic_toggle.setChecked(False)
        self.diagnostic_toggle.hide()
        self.label.clear()
        self.close_button.setDefault(preview)
        if preview:
            self.close_button.setFocus()
        self.preflight_summary.setVisible(preview)
        if preview:
            self.preflight_summary.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.begin.preflight_summary'))
        self._set_primary_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.init.settings_button'), self._open_environment_settings)
        self._clear_actions()
        self.retry_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self._copy_text = ""
        detail = ("正在核对开启同步所需条件；此步骤不会清理或部署组件…" if preview else
                  "正在结束原生连接并清理游戏组件…" if mode in {"offline", "low"} else
                  "正在检测环境并处理配套组件…")
        closing = ("关闭窗口将保持自动同步关闭。" if preview else
                   "关闭窗口不会取消已确认的模式切换和组件处理。")
        pending = plain_label(detail + "\n" + closing, self.results)
        pending.setWordWrap(True)
        self.results_layout.addWidget(pending)
        self.progress.show()
        self.show()
        self.raise_()

    def _add_action(self, title, callback, *, close=False):
        button = QPushButton(title)
        button.setObjectName("btnNew")
        button.setAutoDefault(False)
        def run():
            if close:
                self.accept()
            callback()
        button.clicked.connect(run)
        self.actions.addWidget(button)

    def set_report(self, report):
        self.progress.hide()
        self._clear_actions()
        self._set_primary_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.init.settings_button'), self._open_environment_settings)
        self.retry_button.setEnabled(True)
        self._set_result(report_text(report, text_catalog=getattr(self, 'text_catalog', None)))
        self._render_report(report)
        available = {action for item in report.features for action in item.actions}
        if "manual_deploy" in available and not self._preview:
            self._settings_target = "deployment"
            self._add_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_report.self'), self._open_environment_settings)
            self.settings_button.hide()
        elif "download_npcap" in available:
            def download():
                self.parentWidget()._open_npcap_download()
            self._add_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_report.self_2'), download, close=True)
            self.settings_button.hide()
        elif "detect_game_path" in available:
            def detect():
                self._controller.detect_path()
            self._add_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_report.self_3'), detect, close=True)
            self.settings_button.hide()
        if (not self._preview and self._sync_action_provider is not None
                and report.can_offer_sync_enable):
            action = self._sync_action_provider(report)
            if action is not None:
                self._add_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.prompt_offline_sync_mode.dialog'), action)

    def set_error(self, detail):
        self.status_hint.hide()
        self.progress.hide()
        self._clear_actions()
        self._set_primary_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.init.settings_button'), self._open_environment_settings)
        self.retry_button.setEnabled(True)
        self._set_result(detail)
        self._clear_results()
        self.overview.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_error.overview'))
        self._add_result_section(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.render_report.self'), [(('fault', '故障', '环境检测中断，请查看排查信息。'), ['环境检测'])],
            "#f85149",
        )
        if self._preview:
            self.preflight_summary.setText(
                resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_error.preflight_summary')
            )

    def set_sync_preflight(self, decision):
        self._settings_target = decision.target
        self._clear_actions()
        self.preflight_summary.setText(
            (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary') if decision.ready else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary_2')) +
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary_3') + decision.detail +
            (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary_4') if decision.ready else
             resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary_5') + (decision.action_label or resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary_6')))
        )
        if decision.action_label:
            self._add_action(decision.action_label, self._open_environment_settings)
            self.settings_button.hide()
        elif decision.target == "mode":
            self._set_primary_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.self'), self._open_environment_settings)
        else:
            self._set_primary_action(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.init.settings_button'), self._open_environment_settings)
        self._copy_text += "\n\n开启同步：" + decision.detail
        self.label.setText(self._copy_text)
        if decision.ready:
            self.settings_button.hide()

    def set_activation_result(self, ready, detail):
        self.status_hint.hide()
        self.progress.hide()
        self._clear_actions()
        self.retry_button.setEnabled(True)
        self.preflight_summary.show()
        self.preflight_summary.setText(
            (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_activation_result.preflight_summary') if ready else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_activation_result.preflight_summary_2')) +
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_sync_preflight.preflight_summary_3') + detail +
            (resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_activation_result.preflight_summary_3') if ready else
             resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_activation_result.preflight_summary_4'))
        )
        self._clear_results()
        self._set_result(self.preflight_summary.text())
        self.overview.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_activation_result.overview') if ready else resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_activation_result.overview_2'))
        self.settings_button.setVisible(not ready)

    def _set_result(self, detail):
        header = (localized_text(getattr(self, 'text_catalog', None), 'auto.features.settings.work_mode_card.set_result.f7ff317ea', 'NTE Drive Calc {version} · {value}\n检测时间：{value_2}', version=f'{__version__}', value=f'{self.windowTitle()}', value_2=f"{datetime.now().astimezone().isoformat(timespec='seconds')}"))
        self._copy_text = header + "\n\n" + detail
        self.metadata.setText(header.replace("\n", "  ·  "))
        self.label.setText(self._copy_text)
        self.diagnostic_toggle.show()
        self.copy_button.setEnabled(True)

    def _copy_result(self):
        if self._copy_text:
            QApplication.clipboard().setText(self._copy_text)


class CleanupResultDialog(QDialog):
    """清理结果独立展示，不把环境连接状态当清理成功。"""

    def __init__(self, parent, *, continue_upgrade: bool = False, text_catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self._continue_upgrade = continue_upgrade
        self.setObjectName("gameDirectoryCleanupDialog")
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('settings.page.build_environment_card.equipment_plugin_stop_button'))
        self.setWindowModality(Qt.WindowModal)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(14)
        self.message = plain_label(self)
        self.message.setObjectName("gameDirectoryCleanupMessage")
        self.message.setTextFormat(Qt.PlainText)
        self.message.setWordWrap(True)
        self.message.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.message)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        actions = QHBoxLayout()
        actions.addStretch()
        self.continue_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.settings.init.continue_button'), self)
        self.continue_button.setObjectName("btnNew")
        self.continue_button.setAutoDefault(False)
        self.continue_button.clicked.connect(self.accept)
        self.continue_button.hide()
        actions.addWidget(self.continue_button)
        close_button = TextActionButton(resolve_text_catalog(text_catalog).render('common.close'), self)
        close_button.setDefault(True)
        close_button.clicked.connect(self.reject)
        actions.addWidget(close_button)
        layout.addLayout(actions)
        fit_dialog_to_available_screen(self, QSize(560, 230))

    def begin(self):
        self.message.setText(
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.begin.message')
        )
        self.progress.show()
        self.show()
        self.raise_()

    def set_result(self, *, pending: bool, state: str, detail: str):
        self.progress.hide()
        if state == "fault":
            status = "清理未完成"
            next_step = "按原因处理后，再点击“清理游戏目录”。"
        elif not pending:
            status = "已清理"
            next_step = (
                "点击“继续升级引导”返回引导窗口；关闭后也可从工作台继续。"
                if self._continue_upgrade else
                "如需重新同步，请确认工作模式后再开启自动同步。"
            )
        else:
            status = "等待继续清理"
            next_step = "按原因处理后重试；游戏运行时请先退出游戏。"
        self.message.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.settings.set_result.message', status=ui_enum_text(getattr(self, 'text_catalog', None), f'{status}'), detail=f'{detail}', next_step=ui_enum_text(getattr(self, 'text_catalog', None), f'{next_step}')))
        self.continue_button.setVisible(self._continue_upgrade and not pending and state != "fault")


def build_work_mode_card(window):
    controller = window.work_mode_controller
    service = window.work_mode_service
    card = window._card(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.settings.build_work_mode_card.card'))
    controls = QHBoxLayout()
    controls.setSpacing(10)
    current_label = plain_label(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.settings.build_work_mode_card.current_label'))
    controls.addWidget(current_label)
    combo = NoWheelComboBox()
    for key, label in ui_enum_text(getattr(window, 'text_catalog', None), MODE_LABELS).items():
        combo.addItem(label, key)
    combo.setCurrentIndex(combo.findData(service.settings.mode.value))
    combo.setFixedWidth(150)
    controls.addWidget(combo)
    check = TextActionButton(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.settings.build_work_mode_card.check'))
    check.setFixedWidth(96)
    check.clicked.connect(lambda: controller.check(show=True, retry_deployment=True))
    controls.addWidget(check)
    controls.addStretch()
    card.layout().addLayout(controls)

    mode_labels = {}
    descriptions = QVBoxLayout()
    descriptions.setSpacing(8)
    for key, title in ui_enum_text(getattr(window, 'text_catalog', None), MODE_LABELS).items():
        row = QHBoxLayout()
        row.setSpacing(8)
        mode_label = plain_label(f"{title}：")
        mode_label.setObjectName(f"workModeDescription_{key}")
        mode_label.setFixedWidth(58)
        detail = plain_label(ui_enum_text(getattr(window, 'text_catalog', None), MODE_DESCRIPTIONS)[key])
        detail.setWordWrap(False)
        detail.setStyleSheet(f"color:{theme_color('#c9d1d9')}")
        row.addWidget(mode_label)
        row.addWidget(detail)
        row.addStretch()
        descriptions.addLayout(row)
        mode_labels[key] = mode_label
    card.layout().addLayout(descriptions)

    def refresh_mode_emphasis(_index=None):
        selected = service.settings.mode.value if combo.currentIndex() >= 0 else None
        for key, label in mode_labels.items():
            label.setProperty("confirmedMode", key == selected)
            color = theme_color("#58a6ff" if key == selected else "#f0f6fc")
            label.setStyleSheet(f"color:{color};font-weight:700")

    combo.currentIndexChanged.connect(refresh_mode_emphasis)
    refresh_mode_emphasis()

    # 逐功能状态归显式检测弹窗；隐藏标签只接收 Controller 的窄状态投影。
    status = plain_label(resolve_text_catalog(getattr(window, 'text_catalog', None)).render('ui.settings.build_work_mode_card.status'), card)
    status.hide()
    controller.attach_controls(combo, status, check)

    def select_mode(_index):
        controller.select_mode(combo.currentData())
        refresh_mode_emphasis()

    combo.activated.connect(select_mode)
    return card


def _confirmation_row(title: str, detail: str, tone: str, parent) -> QFrame:
    frame = QFrame(parent)
    frame.setObjectName("workModeConfirmationRow")
    frame.setStyleSheet(
        f"QFrame#workModeConfirmationRow{{background:{theme_color('#161b22')};"
        f"border:1px solid {theme_color('#30363d')};border-radius:8px}}"
    )
    row = QHBoxLayout(frame)
    row.setContentsMargins(14, 11, 14, 11)
    row.setSpacing(12)
    heading = plain_label(title, frame)
    heading.setObjectName(f"workModeConfirmation_{tone}")
    heading.setFixedWidth(76)
    tone_color = {"available": "#3fb950", "unavailable": "#8b949e", "after": "#58a6ff"}[tone]
    heading.setStyleSheet(
        f"color:{theme_color(tone_color)};font-weight:700"
    )
    body = plain_label(detail, frame)
    body.setWordWrap(True)
    row.addWidget(heading)
    row.addWidget(body, 1)
    return frame


def confirm_mode(parent, mode: str, *, text_catalog=None) -> bool:
    copy = ui_enum_text(text_catalog, MODE_CONFIRMATIONS)[mode]
    dialog = QDialog(parent)
    dialog.setObjectName("workModeConfirmationDialog")
    dialog.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.dialog') + ui_enum_text(text_catalog, MODE_LABELS)[mode] + resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.dialog_2'))
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(22, 20, 22, 18)
    layout.setSpacing(12)

    warning = QFrame(dialog)
    warning.setObjectName("workModeWarning")
    warning.setStyleSheet(
        f"QFrame#workModeWarning{{background:{theme_color('#2d1117')};"
        f"border:1px solid {theme_color('#f85149')};border-radius:9px}}"
    )
    warning_row = QHBoxLayout(warning)
    warning_row.setContentsMargins(16, 14, 16, 14)
    warning_row.setSpacing(14)
    icon = plain_label("⚠", warning)
    icon.setObjectName("workModeWarningIcon")
    icon.setStyleSheet(f"color:{theme_color('#f85149')};font-size:30px;font-weight:700")
    warning_row.addWidget(icon)
    warning_text = QVBoxLayout()
    warning_text.setSpacing(3)
    warning_title = plain_label(copy["warning_title"], warning)
    warning_title.setObjectName("workModeWarningTitle")
    warning_title.setStyleSheet(f"color:{theme_color('#f85149')};font-size:16px;font-weight:700")
    warning_detail = plain_label(copy["warning_detail"], warning)
    warning_detail.setObjectName("workModeWarningDetail")
    warning_detail.setWordWrap(True)
    warning_text.addWidget(warning_title)
    warning_text.addWidget(warning_detail)
    warning_row.addLayout(warning_text, 1)
    layout.addWidget(warning)

    layout.addWidget(_confirmation_row(resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.caption'), copy["available"], "available", dialog))
    layout.addWidget(_confirmation_row(resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.caption_2'), copy["unavailable"], "unavailable", dialog))
    layout.addWidget(_confirmation_row(resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.caption_3'), copy["after"], "after", dialog))

    note = plain_label(copy["note"], dialog)
    note.setObjectName("workModeConfirmationNote")
    note.setWordWrap(True)
    note.setStyleSheet(f"color:{theme_color('#8b949e')};font-size:12px")
    layout.addWidget(note)

    buttons = QDialogButtonBox(QDialogButtonBox.Cancel, parent=dialog)
    cancel = buttons.button(QDialogButtonBox.Cancel)
    set_button_text(cancel, resolve_text_catalog(text_catalog).render('common.cancel'))
    cancel.setFixedSize(88, 38)
    consent = buttons.addButton(
        resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.consent') if mode == "offline" else resolve_text_catalog(text_catalog).render('ui.settings.confirm_mode.consent_2'),
        QDialogButtonBox.AcceptRole,
    )
    consent.setObjectName("workModeConfirm" if mode == "offline" else "workModeRiskConsent")
    consent.setMinimumWidth(148)
    consent.setFixedHeight(38)
    consent.setAutoDefault(False)
    consent.setDefault(False)
    if mode != "offline":
        consent.setStyleSheet(
            f"QPushButton{{background:{theme_color('#da3633')};color:#ffffff;"
            f"border:1px solid {theme_color('#f85149')};border-radius:6px;padding:7px 16px;font-weight:700}}"
            f"QPushButton:hover{{background:{theme_color('#f85149')}}}"
            f"QPushButton:pressed{{background:{theme_color('#b42318')}}}"
            f"QPushButton:focus{{border:2px solid {theme_color('#fda29b')};padding:6px 15px}}"
        )
    button_layout = buttons.layout()
    button_layout.removeWidget(cancel)
    button_layout.removeWidget(consent)
    button_layout.addStretch()
    button_layout.addWidget(cancel)
    button_layout.addWidget(consent)
    cancel.setDefault(True)
    cancel.setFocus()
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(720, 440))
    return dialog.exec() == QDialog.Accepted
