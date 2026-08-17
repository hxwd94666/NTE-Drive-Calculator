# 构建工具页面及倒带推荐交互界面。
"""Toolbox page and the custom-rewind recommendation interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QAbstractSpinBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.rewind_shape_recommendation_service import (
    RewindShapeAnalysis,
    RewindShapeRecommendationService,
    RewindTargetRole,
)
from src.features.toolbox.rewind_execution_dialog import RewindExecutionOptions
from src.features.toolbox.rewind_execution_ui import RewindExecutionUiMixin
from src.features.toolbox.rewind_slot_ui import RewindSlotUiMixin


@dataclass(frozen=True, slots=True)
class ToolboxDependencies:
    """Narrow account-bound dependencies assembled by ``src.ui.app``."""

    rewind_service_factory: Callable[[], RewindShapeRecommendationService]

    def rewind_service(self) -> RewindShapeRecommendationService:
        return self.rewind_service_factory()


@dataclass(frozen=True, slots=True)
class _RewindUiCatalog:
    roles: tuple[RewindTargetRole, ...]
    owned_shape_counts: tuple[tuple[str, int], ...]


def _preference_custom_percent(value: object) -> float | None:
    """Read a persisted optional custom rewind threshold without trusting old data."""

    if isinstance(value, bool):
        return None
    if not isinstance(value, (str, int, float)):
        return None
    try:
        percent = float(value)
    except (TypeError, ValueError):
        return None
    return percent if 1.0 <= percent <= 100.0 else None


class ToolboxPage:
    """Builds a tile-based toolbox without introducing another primary domain."""

    def __init__(self, *, dependencies: ToolboxDependencies, dialog_parent: QWidget) -> None:
        self._dependencies = dependencies
        self._dialog_parent = dialog_parent
        self._page: QWidget | None = None

    def build(self) -> QWidget:
        if self._page is not None:
            return self._page
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 28, 30, 28)
        layout.setSpacing(12)

        tool_row = QFrame(page)
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
        layout.addStretch()
        self._page = page
        return page

    def refresh(self) -> None:
        """The page stores no account data; analysis is rebuilt on every open."""

    def _show_rewind_recommendation(self) -> None:
        try:
            service = self._dependencies.rewind_service()
        except Exception as exc:
            QMessageBox.warning(self._dialog_parent, "倒带推荐", f"读取倒带分析数据失败：{exc}")
            return
        dialog = _RewindRecommendationDialog(service, self._dialog_parent)
        dialog.exec()


class _RoleSelectionDialog(QDialog):
    """Avatar-card picker shared by target-role and main-role selections."""

    def __init__(
        self,
        parent: QWidget,
        *,
        title: str,
        description: str,
        roles: tuple[RewindTargetRole, ...],
        selected_character_ids: set[int],
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 620)
        self._cards: list[tuple[QToolButton, int, str]] = []
        selected = {int(value) for value in selected_character_ids}
        catalog = GameUiAssetCatalog(bundled_game_ui_asset_root())

        root = QVBoxLayout(self)
        root.setSpacing(10)
        note = QLabel(description)
        note.setWordWrap(True)
        note.setStyleSheet(themed_style("color:#8b949e"))
        root.addWidget(note)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索角色（支持拼音）")
        self.search_edit.textChanged.connect(self._apply_filter)
        root.addWidget(self.search_edit)

        toolbar = QHBoxLayout()
        select_all = QPushButton("全选")
        clear_all = QPushButton("清空")
        select_all.clicked.connect(lambda: self._set_visible_checked(True))
        clear_all.clicked.connect(lambda: self._set_visible_checked(False))
        toolbar.addWidget(select_all)
        toolbar.addWidget(clear_all)
        toolbar.addStretch()
        self.count_label = QLabel()
        self.count_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
        toolbar.addWidget(self.count_label)
        root.addLayout(toolbar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        self._grid = QGridLayout(content)
        self._grid.setContentsMargins(4, 4, 4, 4)
        self._grid.setHorizontalSpacing(8)
        self._grid.setVerticalSpacing(8)
        self._grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        for role in roles:
            card = QToolButton(content)
            card.setObjectName("rewindRoleSelectionCard")
            card.setCheckable(True)
            card.setChecked(role.character_id in selected)
            card.setText(role.name)
            card.setToolTip(role.name)
            avatar_path = catalog.character_icon(role.character_id)
            if avatar_path is not None:
                card.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
                card.setIconSize(QSize(76, 76))
                card.setFixedSize(116, 116)
                card.setIcon(QIcon(str(avatar_path)))
            else:
                # Account-defined roles have no game portrait; show their name
                # directly instead of reserving a misleading empty image card.
                card.setToolButtonStyle(Qt.ToolButtonTextOnly)
                card.setFixedSize(116, 44)
            card.setStyleSheet(themed_style(
                "QToolButton{background:#161b22;color:#c9d1d9;border:1px solid #30363d;"
                "border-radius:8px;padding:6px;font-size:12px;font-weight:700;}"
                "QToolButton:hover{border-color:#58a6ff;background:#1f6feb22;}"
                "QToolButton:checked{border:2px solid #58a6ff;background:#1f6feb;color:#fff;}"
            ))
            card.toggled.connect(self._update_count)
            self._cards.append((card, role.character_id, role.name))
        self._reflow_cards()
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self._update_count()

    def _apply_filter(self, text: str) -> None:
        self._reflow_cards(str(text or "").strip())

    def _reflow_cards(self, keyword: str = "") -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [
            row for row in self._cards
            if not keyword or _matches_role_name(row[2], keyword)
        ]
        for card, _character_id, _name in self._cards:
            card.setVisible(False)
        for index, (card, _character_id, _name) in enumerate(visible):
            self._grid.addWidget(card, index // 5, index % 5)
            card.setVisible(True)

    def _set_visible_checked(self, checked: bool) -> None:
        for card, _character_id, _name in self._cards:
            if not card.isHidden():
                card.setChecked(checked)
        self._update_count()

    def _update_count(self, _checked: bool | None = None) -> None:
        self.count_label.setText(f"已选 {len(self.selected_character_ids())} 名")

    def selected_character_ids(self) -> tuple[int, ...]:
        return tuple(
            character_id
            for card, character_id, _role_name in self._cards
            if card.isChecked()
        )


class _RewindRecommendationDialog(RewindExecutionUiMixin, RewindSlotUiMixin, QDialog):
    """Immediate shell for the rewind advisor; data work stays off the UI thread."""

    _strategy_labels = {
        "balanced": "全面均衡",
        "focused": "少角冲分",
    }

    def __init__(self, service: RewindShapeRecommendationService, parent: QWidget) -> None:
        super().__init__(parent)
        self._service = service
        self._roles: tuple[RewindTargetRole, ...] = ()
        self._role_names: dict[int, str] = {}
        self._target_character_ids: set[int] = set()
        self._main_character_ids: set[int] = set()
        self._strategy_key = "balanced"
        preferences = getattr(service, "load_preferences", lambda: {})()
        self._target_character_ids = {int(value) for value in preferences.get("target_character_ids", ())}
        self._main_character_ids = {int(value) for value in preferences.get("main_character_ids", ())}
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
        self.setMinimumWidth(760)

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
        button = QPushButton("选择冲分角色" if main else "选择角色")
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

    def _load_roles_async(self) -> None:
        if self._roles_worker is not None and self._roles_worker.isRunning():
            return
        self._target_summary.setText("正在加载角色列表…")
        self._main_summary.setText("正在加载角色列表…")
        worker = WorkerThread(target=self._load_role_and_inventory_catalog, parent=self)
        self._roles_worker = worker
        worker.result_ready.connect(self._on_roles_loaded)
        worker.error.connect(self._on_roles_load_error)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _load_role_and_inventory_catalog(self) -> _RewindUiCatalog:
        role_loader = getattr(self._service, "list_target_roles", None)
        count_loader = getattr(self._service, "load_owned_shape_counts", None)
        return _RewindUiCatalog(
            roles=role_loader() if callable(role_loader) else (),
            owned_shape_counts=count_loader() if callable(count_loader) else (),
        )

    def _on_roles_loaded(self, result: object) -> None:
        if isinstance(result, _RewindUiCatalog):
            roles = result.roles
            owned_shape_counts = result.owned_shape_counts
        else:
            roles = result
            owned_shape_counts = ()
        self._roles = tuple(roles)
        self._role_names = {role.character_id: role.name for role in self._roles}
        self._set_replacement_inventory_counts(dict(owned_shape_counts))
        self._target_button.setEnabled(True)
        self._main_button.setEnabled(True)
        self._update_role_summaries()

    def _on_roles_load_error(self, message: str) -> None:
        self._target_summary.setText("角色列表加载失败")
        self._main_summary.setText("角色列表加载失败")

    def _choose_target_roles(self) -> None:
        if not self._roles:
            return
        dialog = _RoleSelectionDialog(
            self,
            title="选择意向培养角色",
            description="选择希望培养的角色。倒带会优先覆盖这些角色所需卡带的驱动形状。",
            roles=self._roles,
            selected_character_ids=self._target_character_ids,
        )
        if dialog.exec() == QDialog.Accepted:
            self._target_character_ids = set(dialog.selected_character_ids())
            self._save_preferences()
            self._update_role_summaries()

    def _choose_main_roles(self) -> None:
        if not self._roles:
            return
        dialog = _RoleSelectionDialog(
            self,
            title="选择冲分角色",
            description="可多选。少角冲分只分析这些角色低于目标等级的已装配驱动。",
            roles=self._roles,
            selected_character_ids=self._main_character_ids,
        )
        if dialog.exec() == QDialog.Accepted:
            self._main_character_ids = set(dialog.selected_character_ids())
            self._target_character_ids.update(self._main_character_ids)
            self._save_preferences()
            self._update_role_summaries()

    def _save_preferences(self) -> None:
        saver = getattr(self._service, "save_preferences", None)
        if callable(saver):
            preferences = dict(getattr(self._service, "load_preferences", lambda: {})())
            preferences.update({
                "target_character_ids": sorted(self._target_character_ids),
                "main_character_ids": sorted(self._main_character_ids),
                "strategy": self._strategy_key,
                "target_grade": self._target_grade,
                "target_threshold_mode": self._target_threshold_mode,
                "target_custom_percent": self._target_custom_percent,
                "rewind_qualities": list(self._rewind_options.qualities),
                "rewind_drive_customization": self._rewind_options.drive_customization,
            })
            saver(preferences)

    def _update_role_summaries(self) -> None:
        if not self._roles and self._roles_worker is not None:
            return
        self._target_summary.setText(self._role_summary(self._target_character_ids, "未选择，请选择角色"))
        self._main_summary.setText(self._role_summary(self._main_character_ids, "未选择，请选择角色"))

    def _role_summary(self, character_ids: set[int], empty_text: str) -> str:
        names = [self._role_names[identifier] for identifier in sorted(character_ids) if identifier in self._role_names]
        if not names:
            return empty_text
        shown = "、".join(names[:3])
        return shown if len(names) <= 3 else f"{shown} 等 {len(names)} 名"

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

    def _refresh_analysis(self) -> None:
        if self._target_threshold_mode == "custom" and self._target_custom_percent is None:
            message = "请选择自选评分百分比（1.0%～100.0%）。"
            QMessageBox.warning(self, "生成推荐", message)
            self._render_message("未生成推荐", message)
            return
        token = object()
        self._analysis_token = token
        self._generate_button.setEnabled(False)
        self._generate_button.setText("分析中…")
        self._render_loading()
        target_ids = tuple(sorted(self._target_character_ids))
        primary_ids = tuple(sorted(self._main_character_ids))
        strategy = self._strategy_value()
        worker = WorkerThread(
            target=lambda: self._service.analyze_for_targets(
                target_character_ids=target_ids,
                strategy=strategy,
                primary_character_ids=primary_ids,
                selection_limit=8,
                target_grade=self._target_grade,
                target_custom_percent=(
                    self._target_custom_percent
                    if self._target_threshold_mode == "custom"
                    else None
                ),
            ),
            parent=self,
        )
        self._analysis_worker = worker
        worker.result_ready.connect(lambda analysis, current=token: self._on_analysis_ready(current, analysis))
        worker.error.connect(lambda message, current=token: self._on_analysis_error(current, message))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_analysis_ready(self, token: object, analysis: object) -> None:
        if token is not self._analysis_token or not isinstance(analysis, RewindShapeAnalysis):
            return
        self._generate_button.setEnabled(True)
        self._generate_button.setText("生成方案")
        if analysis.notice:
            self._render_message("推荐提示", analysis.notice)
            return
        self._render_plans(analysis)

    def _on_analysis_error(self, token: object, message: str) -> None:
        if token is not self._analysis_token:
            return
        self._generate_button.setEnabled(True)
        self._generate_button.setText("重新生成")
        QMessageBox.warning(self, "生成推荐", message)
        self._render_message("未生成推荐", message)

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


def _matches_role_name(name: str, keyword: str) -> bool:
    from src.ui.widgets import match_pinyin

    return match_pinyin(name, keyword)


def build_toolbox_page(window) -> QWidget:
    """Build through the instance assembled at the application composition root."""

    return window.toolbox_page.build()


def refresh_toolbox_page(window) -> None:
    window.toolbox_page.refresh()
