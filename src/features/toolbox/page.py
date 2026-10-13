# 构建工具页面及倒带推荐交互界面。
"""Toolbox page and the custom-rewind recommendation interface."""

from __future__ import annotations

from src.features.toolbox.rewind.role_selection import preference_custom_percent as _preference_custom_percent
from src.domain.rewind_loadout import read_slot_preferences

from typing import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QAbstractSpinBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.app.workers import WorkerThread
from src.features.toolbox.cultivation.entry import (
    build_cultivation_calculator_entry,
)
from src.services.rewind.recommendation import (
    RewindShapeAnalysis,
    RewindShapeRecommendationService,
    RewindTargetRole,
)
from src.features.toolbox.rewind.execution_dialog import RewindExecutionOptions
from src.features.toolbox.rewind.execution_ui import RewindExecutionUiMixin
from src.features.toolbox.rewind.selection_ui import RewindSelectionUiMixin
from src.features.toolbox.rewind.slot_ui import RewindSlotUiMixin
from src.features.toolbox.static_catalog_entry import build_static_catalog_entry
from src.features.toolbox.toolbox_navigation import (
    CultivationToolboxNavigation,
    ToolboxDependencies,
)

class ToolboxPage:
    """Builds a tile-based toolbox without introducing another primary domain."""

    def __init__(self, *, dependencies: ToolboxDependencies, dialog_parent: QWidget) -> None:
        self._dependencies = dependencies
        self._dialog_parent = dialog_parent
        self._page: QWidget | None = None
        self._stack: QStackedWidget | None = None
        self._home: QWidget | None = None
        self._cultivation_navigation: CultivationToolboxNavigation | None = None

    def build(self) -> QWidget:
        if self._page is not None:
            return self._page
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self._stack = QStackedWidget(page)
        self._stack.setObjectName("toolboxPageStack")
        page_layout.addWidget(self._stack)

        home = QWidget(self._stack)
        home.setObjectName("toolboxHomePage")
        layout = QVBoxLayout(home)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(12)

        tool_row = QFrame(home)
        tool_row.setObjectName("toolboxRewindRecommendationRow")
        tool_row.setMinimumHeight(94)
        tool_row.setStyleSheet(themed_style(
            "QFrame#toolboxRewindRecommendationRow{background:#161b22;border:1px solid #30363d;"
            "border-radius:10px;}QFrame#toolboxRewindRecommendationRow:hover{background:#1c2128;border-color:#58a6ff;}"
        ))
        row_layout = QHBoxLayout(tool_row)
        row_layout.setContentsMargins(18, 12, 16, 12)
        row_layout.setSpacing(15)

        copy = QVBoxLayout()
        copy.setSpacing(4)
        title = QLabel("倒带推荐", tool_row)
        title.setObjectName("toolboxRewindRecommendationTitle")
        title.setStyleSheet(themed_style("font-size:16px;font-weight:800;color:#58a6ff"))
        copy.addWidget(title)
        description = QLabel("根据培养角色、目标评分与当前库存，生成 8 个驱动形状的自定义抽取方案。", tool_row)
        description.setWordWrap(True)
        description.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        copy.addWidget(description)
        row_layout.addLayout(copy, 1)

        use_button = QPushButton("使用", tool_row)
        use_button.setObjectName("toolboxRewindRecommendation")
        use_button.setCursor(Qt.PointingHandCursor)
        use_button.setMinimumSize(76, 38)
        use_button.setStyleSheet(themed_style(
            "QPushButton{background:#d6f0ff;color:#0b3150;border:1px solid #79c0ff;border-radius:7px;"
            "font-size:13px;font-weight:800;padding:6px 16px;}"
            "QPushButton:hover{background:#b6e3ff;border-color:#a5d6ff;}"
            "QPushButton:pressed{background:#9ed5f5;}"
        ))
        use_button.clicked.connect(self._show_rewind_recommendation)
        row_layout.addWidget(use_button, 0, Qt.AlignVCenter)

        layout.addWidget(tool_row)
        layout.addWidget(build_cultivation_calculator_entry(
            home,
            open_calculator=self._open_cultivation_calculator,
        ))
        layout.addWidget(build_static_catalog_entry(
            home,
            navigate=self._dependencies.navigate_static_catalog,
        ))
        layout.addStretch()
        self._home = home
        self._stack.addWidget(home)
        self._cultivation_navigation = CultivationToolboxNavigation(
            stack=self._stack,
            home=home,
            dependencies=self._dependencies,
            dialog_parent=self._dialog_parent,
        )
        self._page = page
        return page
    def refresh(self) -> None:
        """Discard an account-bound draft only when its frozen identity changed."""

        if self._cultivation_navigation is not None:
            self._cultivation_navigation.refresh()

    def shutdown(self) -> None:
        """撤销养成草稿与排队动作；退场页仍由父对象保留到后台终态。"""
        if self._cultivation_navigation is not None:
            self._cultivation_navigation.discard()

    def _open_cultivation_calculator(self) -> None:
        if self._cultivation_navigation is not None:
            self._cultivation_navigation.open()

    def _show_rewind_recommendation(self) -> None:
        try:
            service = self._dependencies.rewind_service()
        except Exception as exc:
            QMessageBox.warning(self._dialog_parent, "倒带推荐", f"读取倒带分析数据失败：{exc}")
            return
        dialog = _RewindRecommendationDialog(service, self._dialog_parent, operation_guard=self._dependencies.operation_guard, operation_generation=self._dependencies.operation_generation, operation_entry=self._dependencies.operation_entry, operation_unavailable=self._dependencies.operation_unavailable)
        dialog.exec()
class _RewindRecommendationDialog(RewindSelectionUiMixin, RewindExecutionUiMixin, RewindSlotUiMixin, QDialog):
    """Immediate shell for the rewind advisor; data work stays off the UI thread."""

    _strategy_labels = {
        "balanced": "全面均衡",
        "focused": "少角冲分",
    }

    def __init__(self, service: RewindShapeRecommendationService, parent: QWidget, *, operation_guard: Callable[[str], None] | None = None, operation_generation: Callable[[], object] | None = None, operation_entry: Callable[[str, str], bool] | None = None, operation_unavailable: Callable[[str, str, str], None] | None = None) -> None:
        super().__init__(parent)
        self.operation_guard = operation_guard
        self.operation_generation = operation_generation
        self.operation_entry = operation_entry
        self.operation_unavailable = operation_unavailable
        self._service = service
        self._initialize_selection_lifecycle()
        self._roles: tuple[RewindTargetRole, ...] = ()
        self._role_names: dict[int, str] = {}
        self._target_character_ids: set[int] = set()
        self._main_character_ids: set[int] = set()
        self._strategy_key = "balanced"
        preferences = getattr(service, "load_preferences", lambda: {})()
        self._target_character_ids = {int(value) for value in preferences.get("target_character_ids", ())}
        self._main_character_ids = {int(value) for value in preferences.get("main_character_ids", ())}
        self._saved_slot_ids_by_strategy = {
            key: read_slot_preferences(preferences, strategy=key) for key in ("balanced", "focused")
        }
        self._selected_slots_by_strategy = {"balanced": {}, "focused": {}}
        self._strategy_key = str(preferences.get("strategy", self._strategy_key))
        self._target_grade = str(preferences.get("target_grade", "S"))
        self._target_threshold_mode = str(preferences.get("target_threshold_mode", "grade"))
        self._target_custom_percent = _preference_custom_percent(
            preferences.get("target_custom_percent"),
        )
        if self._target_threshold_mode not in {"grade", "custom"}:
            self._target_threshold_mode = "grade"
        self._roles_worker: WorkerThread | None = None
        self._analysis_worker: WorkerThread | None = None
        self._analysis_token: object | None = None
        self._rewind_options = RewindExecutionOptions(
            qualities=tuple(str(value) for value in preferences.get("rewind_qualities", ("gold",))),
            drive_customization=str(preferences.get("rewind_drive_customization", "none")),
        )
        self._saved_rewind_shape_ids = tuple(str(value) for value in preferences.get("saved_rewind_shape_ids", ()))
        self._saved_rewind_slots = tuple(preferences.get("saved_rewind_slots", ()))
        self._rewind_worker: WorkerThread | None = None

        self.setWindowTitle("倒带推荐")
        self.resize(900, 700)
        self.setMinimumWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        layout.addWidget(self._build_controls())

        self._result_tabs = QTabWidget()
        self._result_tabs.setObjectName("rewindRecommendationPlans")
        self._result_tabs.tabBar().hide()
        layout.addWidget(self._result_tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        self._save_plan_button = QPushButton("保存方案")
        self._save_plan_button.setObjectName("rewindSavePlan")
        self._save_plan_button.setEnabled(False)
        self._save_plan_button.clicked.connect(self._save_plan)
        buttons.addWidget(self._save_plan_button)
        self._start_rewind_button = QPushButton("进行倒带")
        self._start_rewind_button.setObjectName("rewindStartRun")
        self._start_rewind_button.setEnabled(False)
        self._start_rewind_button.clicked.connect(self._configure_rewind)
        buttons.addWidget(self._start_rewind_button)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        # Let the modal paint before opening the two read-only database jobs.
        QTimer.singleShot(0, self, self._load_roles_async)
        fit_dialog_to_available_screen(self, QSize(900, 700))
        self._initialize_rewind_slots(
            self._saved_rewind_shape_ids,
            self._saved_rewind_slots,
        )

    def _build_controls(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("rewindRecommendationControls")
        panel.setStyleSheet(themed_style(
            "QFrame#rewindRecommendationControls{background:#161b22;border:1px solid #30363d;"
            "border-radius:12px;padding:0;}"
            "QFrame#rewindSelectionCard{background:#0d1117;border:1px solid #21262d;border-radius:9px;}"
            "QPushButton#rewindStrategy{background:#21262d;color:#8b949e;border:1px solid #30363d;"
            "border-radius:7px;padding:7px 12px;font-weight:600;}"
            "QPushButton#rewindStrategy:hover{background:#30363d;color:#c9d1d9;}"
            "QPushButton#rewindStrategy:checked{background:#1f6feb33;color:#58a6ff;border-color:#58a6ff;}"
        ))
        root = QVBoxLayout(panel)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._build_role_card(
            title="培养角色",
            description="仅在全面均衡策略中生效",
            main=False,
        ), 1)
        top.addWidget(self._build_role_card(
            title="冲分角色",
            description="仅在少角冲分策略中生效",
            main=True,
        ), 1)
        root.addLayout(top)

        strategy_row = QHBoxLayout()
        strategy_title = QLabel("培养策略")
        strategy_title.setStyleSheet(themed_style("font-weight:700;color:#f0f6fc"))
        strategy_row.addWidget(strategy_title)
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setToolTip("查看两种培养策略的差异")
        help_button.clicked.connect(self._show_strategy_help)
        strategy_row.addWidget(help_button)
        strategy_row.addSpacing(6)

        self._strategy_group = QButtonGroup(self)
        self._strategy_buttons: dict[str, QPushButton] = {}
        for label, value in (("全面均衡", "balanced"), ("少角冲分", "focused")):
            button = QPushButton(label)
            button.setObjectName("rewindStrategy")
            button.setCheckable(True)
            button.setChecked(value == self._strategy_key)
            button.clicked.connect(lambda _checked=False, key=value: self._set_strategy(key))
            self._strategy_group.addButton(button)
            self._strategy_buttons[value] = button
            strategy_row.addWidget(button)
        strategy_row.addStretch(1)
        root.addLayout(strategy_row)
        grade_row = QHBoxLayout()
        grade_row.addWidget(QLabel("评分等级"))
        self._grade_buttons: dict[str, QPushButton] = {}
        self._grade_group = QButtonGroup(self)
        for grade in ("D", "C", "B", "A", "S", "SS", "SSS", "ACE"):
            button = QPushButton(grade)
            button.setObjectName("rewindStrategy")
            button.setCheckable(True)
            button.setChecked(self._target_threshold_mode == "grade" and grade == self._target_grade)
            button.clicked.connect(lambda _checked=False, value=grade: self._set_target_grade(value))
            self._grade_group.addButton(button)
            self._grade_buttons[grade] = button
            grade_row.addWidget(button)
        self._custom_target_button = QPushButton("自选")
        self._custom_target_button.setObjectName("rewindStrategy")
        self._custom_target_button.setCheckable(True)
        self._custom_target_button.setChecked(self._target_threshold_mode == "custom")
        self._custom_target_button.clicked.connect(self._set_custom_target)
        self._grade_group.addButton(self._custom_target_button)
        grade_row.addWidget(self._custom_target_button)
        self._custom_percent_input = QDoubleSpinBox()
        self._custom_percent_input.setObjectName("rewindCustomPercent")
        self._custom_percent_input.setRange(0.0, 100.0)
        self._custom_percent_input.setDecimals(1)
        self._custom_percent_input.setSingleStep(0.1)
        self._custom_percent_input.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self._custom_percent_input.setSpecialValueText("")
        self._custom_percent_input.setSuffix("%" if self._target_custom_percent is not None else "")
        self._custom_percent_input.setFixedWidth(60)
        self._custom_percent_input.setValue(self._target_custom_percent or 0.0)
        if self._target_custom_percent is None:
            self._custom_percent_input.lineEdit().clear()
        self._custom_percent_input.valueChanged.connect(self._set_custom_percent)
        percent_control = QWidget()
        percent_control.setFixedWidth(82)
        percent_layout = QHBoxLayout(percent_control)
        percent_layout.setContentsMargins(0, 0, 0, 0)
        percent_layout.setSpacing(2)
        percent_layout.addWidget(self._custom_percent_input)
        percent_steps = QVBoxLayout()
        percent_steps.setContentsMargins(0, 0, 0, 0)
        percent_steps.setSpacing(2)
        self._custom_percent_step_up = QToolButton()
        self._custom_percent_step_up.setObjectName("rewindPercentStepUp")
        self._custom_percent_step_up.setText("▲")
        self._custom_percent_step_up.setToolTip("增加 0.1%")
        self._custom_percent_step_up.clicked.connect(self._custom_percent_input.stepUp)
        self._custom_percent_step_down = QToolButton()
        self._custom_percent_step_down.setObjectName("rewindPercentStepDown")
        self._custom_percent_step_down.setText("▼")
        self._custom_percent_step_down.setToolTip("减少 0.1%")
        self._custom_percent_step_down.clicked.connect(self._custom_percent_input.stepDown)
        percent_steps.addWidget(self._custom_percent_step_up)
        percent_steps.addWidget(self._custom_percent_step_down)
        percent_layout.addLayout(percent_steps)
        self._set_custom_percent_controls_enabled(self._target_threshold_mode == "custom")
        grade_row.addWidget(percent_control)
        grade_help_button = QPushButton("?")
        grade_help_button.setObjectName("btnHelp")
        grade_help_button.setToolTip("查看评分等级对应的目标百分比")
        grade_help_button.clicked.connect(self._show_grade_help)
        grade_row.addWidget(grade_help_button)
        grade_row.addStretch(1)
        self._generate_button = QPushButton("生成方案")
        self._generate_button.setObjectName("btnPrimary")
        self._generate_button.clicked.connect(self._refresh_analysis)
        grade_row.addWidget(self._generate_button)
        root.addLayout(grade_row)

        self._update_role_summaries()
        return panel

    def _build_role_card(self, *, title: str, description: str, main: bool) -> QWidget:
        card = QFrame()
        card.setObjectName("rewindSelectionCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 10, 10)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setStyleSheet(themed_style("font-weight:700;color:#f0f6fc"))
        layout.addWidget(title_label)
        description_label = QLabel(description)
        description_label.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        layout.addWidget(description_label)
        selection_row = QHBoxLayout()
        summary = QLabel()
        summary.setWordWrap(False)
        summary.setStyleSheet(themed_style("color:#c9d1d9"))
        selection_row.addWidget(summary, 1)
        button = QPushButton("选择角色")
        button.setObjectName("btnAction")
        button.setEnabled(False)
        button.clicked.connect(self._choose_main_roles if main else self._choose_target_roles)
        selection_row.addWidget(button)
        layout.addLayout(selection_row)
        if main:
            self._main_summary = summary
            self._main_button = button
        else:
            self._target_summary = summary
            self._target_button = button
        return card

    def _set_strategy(self, value: object) -> None:
        # Accept QAction too to keep callers from the earlier drop-down implementation working.
        if hasattr(value, "data"):
            key = str(value.data())
        else:
            key = str(value)
        if key not in self._strategy_labels:
            return
        self._strategy_key = key
        for button_key, button in self._strategy_buttons.items():
            button.setChecked(button_key == key)
        self._save_preferences()

    def _set_target_grade(self, grade: str) -> None:
        self._target_threshold_mode = "grade"
        self._target_grade = grade
        for value, button in self._grade_buttons.items():
            button.setChecked(value == grade)
        self._custom_target_button.setChecked(False)
        self._set_custom_percent_controls_enabled(False)
        self._save_preferences()

    def _set_custom_target(self, _checked: bool = False) -> None:
        self._target_threshold_mode = "custom"
        for button in self._grade_buttons.values():
            button.setChecked(False)
        self._custom_target_button.setChecked(True)
        self._set_custom_percent_controls_enabled(True)
        self._custom_percent_input.setFocus()
        self._save_preferences()

    def _set_custom_percent(self, value: float) -> None:
        self._target_custom_percent = float(value) if value >= 1.0 else None
        self._custom_percent_input.setSuffix("%" if self._target_custom_percent is not None else "")
        if self._target_custom_percent is None:
            self._custom_percent_input.lineEdit().clear()
        if self._target_threshold_mode == "custom":
            self._save_preferences()

    def _set_custom_percent_controls_enabled(self, enabled: bool) -> None:
        self._custom_percent_input.setEnabled(enabled)
        self._custom_percent_step_up.setEnabled(enabled)
        self._custom_percent_step_down.setEnabled(enabled)

    def _strategy_value(self) -> str:
        return self._strategy_key

    def _show_strategy_help(self) -> None:
        QMessageBox.information(
            self,
            "培养策略说明",
            "· 全面均衡：优先补培养角色中没达到目标评分的驱动，并兼顾库存\n"
            "· 少角冲分：只看冲分角色，优先补缺分更多的驱动",
        )

    def _show_grade_help(self) -> None:
        message = QMessageBox(self)
        message.setWindowTitle("自选评分等级说明")
        message.setIcon(QMessageBox.Icon.NoIcon)
        message.setText(
            "D：0%\n"
            "C：20%\n"
            "B：30%\n"
            "A：40%\n"
            "S：50%\n"
            "SS：60%\n"
            "SSS：70%\n"
            "ACE：80%\n"
            "自选：以填写百分比为准",
        )
        message.exec()

    def _render_loading(self) -> None:
        self._render_message("正在生成推荐", "已打开倒带推荐，正在后台读取快照并匹配角色评分质量…")

    def _render_message(self, title: str, detail: str) -> None:
        while self._result_tabs.count():
            tab = self._result_tabs.widget(0)
            self._result_tabs.removeTab(0)
            if tab is not None:
                tab.deleteLater()
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(18, 16, 18, 16)
        box.addStretch()
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet(themed_style("font-size:16px;font-weight:700;color:#f0f6fc"))
        box.addWidget(title_label)
        detail_label = QLabel(detail)
        detail_label.setAlignment(Qt.AlignCenter)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet(themed_style("color:#8b949e"))
        box.addWidget(detail_label)
        box.addStretch()
        self._result_tabs.addTab(page, "推荐结果")

    def _render_plans(self, analysis: RewindShapeAnalysis) -> None:
        recommendations = tuple(
            recommendation
            for plan in analysis.plans
            for recommendation in plan.recommendations
        ) or analysis.recommendations
        self._set_replacement_inventory_counts(dict(analysis.owned_shape_counts))
        self._apply_recommendations(recommendations)
        description = {
            "balanced": "全面均衡推荐已生成；可继续手动调整八个槽位。",
            "focused": "少角冲分推荐已生成；可继续手动调整八个槽位。",
        }.get(analysis.strategy, "推荐已生成；可继续手动调整八个槽位。")
        self._render_rewind_slots("推荐方案", description)


def build_toolbox_page(window) -> QWidget:
    """Build through the instance assembled at the application composition root."""

    return window.toolbox_page.build()


def refresh_toolbox_page(window) -> None:
    window.toolbox_page.refresh()
