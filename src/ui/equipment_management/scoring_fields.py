# 复用评分字段视图，不拥有账号持久化、任务或扫描／仓库的业务执行。
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from src.app.theme import themed_style
from src.domain.post_actions import GRADE_ORDER, DEFAULT_EXCLUDED_SET_NAMES, DEFAULT_EXCLUDED_SHAPE_IDS
from src.ui.widgets import NoWheelComboBox
from src.ui.equipment_management.scope_dialogs import RoleScopeDialog, TypeRangeDialog, type_range_summary

ROLE_SCOPE_OPTIONS = (("所有角色", "all"), ("所选角色", "selected"))

QUALITY_SCOPE_OPTIONS = (("全部", "all"), ("仅金品质", "gold"), ("仅金紫品质", "gold_purple"))

TYPE_SCOPE_OPTIONS = (("全部", "all"), ("仅驱动", "drive"), ("仅卡带", "tape"))

STATE_ACTION_OPTIONS = (("跳过", "skip"), ("正常处理", "normal"))


def _set_combo_data(combo: NoWheelComboBox, value: object) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return


def _combo(options, value: object, width: int = 130) -> NoWheelComboBox:
    combo = NoWheelComboBox()
    for label, data in options:
        combo.addItem(label, data)
    _set_combo_data(combo, value)
    combo.setMaximumWidth(width)
    return combo


class ScoringFields:
    def _style_toggle_button(self, button: QPushButton, checked: bool) -> None:
        button.setText("开启中" if checked else "关闭中")
        if checked:
            button.setStyleSheet(
                "QPushButton{background:#238636;color:#fff;border:1px solid #2ea043;"
                "border-radius:12px;padding:4px 14px;font-weight:700;}"
                "QPushButton:hover{background:#2ea043;}"
            )
        else:
            button.setStyleSheet(
                "QPushButton{background:#da3633;color:#fff;border:1px solid #f85149;"
                "border-radius:12px;padding:4px 14px;font-weight:700;}"
                "QPushButton:hover{background:#f85149;}"
            )

    def _make_toggle_button(self, checked: bool) -> QPushButton:
        button = QPushButton()
        button.setCheckable(True)
        button.setChecked(checked)
        button.setFixedWidth(72)
        self._style_toggle_button(button, checked)
        button.toggled.connect(lambda value, b=button: self._style_toggle_button(b, value))
        return button

    def _build_scoring_page(self) -> QWidget:
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 12, 0, 0)
        root.setSpacing(12)
        modules = QHBoxLayout()
        modules.setSpacing(14)
        modules.addWidget(self._build_module_panel("discard", "弃置模块", "最高评分低于等于"), 1)
        modules.addWidget(self._build_module_panel("lock", "锁定模块", "最高评分高于等于"), 1)
        root.addLayout(modules, 1)
        if self._show_server_region_option:
            root.addLayout(self._scoring_footer)
        return page

    def _module_help_text(self, key: str) -> str:
        return self.catalog.render("scan.management.help." + key)

    def _show_module_help(self, key: str, title: str) -> None:
        QMessageBox.information(self, f"{title}说明", self._module_help_text(key))

    def _build_module_panel(self, key: str, title: str, grade_label: str) -> QWidget:
        module = self.config[key]
        panel = QWidget()
        panel.setObjectName("postActionPanel")
        panel.setStyleSheet(
            themed_style("QWidget#postActionPanel{background:#161b22;border:1px solid #30363d;border-radius:8px;}")
        )
        outer = QVBoxLayout(panel)
        outer.setContentsMargins(14, 12, 14, 14)
        outer.setSpacing(10)
        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setStyleSheet("font-size:14px;font-weight:700;color:#58a6ff")
        help_button = QPushButton("?")
        help_button.setObjectName("btnHelp")
        help_button.setFixedWidth(28)
        help_button.clicked.connect(lambda _checked=False, k=key, t=title: self._show_module_help(k, t))
        enabled = self._make_toggle_button(bool(module.get("enabled")))
        header.addWidget(title_label)
        header.addWidget(help_button)
        header.addStretch()
        header.addWidget(enabled)
        outer.addLayout(header)

        form = QFormLayout()
        grade_combo = _combo([(grade, grade) for grade in GRADE_ORDER], module.get("grade"), 100)
        threshold_mode = _combo((("评级", "grade"), ("自定义", "percent")), module.get("threshold_mode", "grade"), 92)
        threshold_percent = QDoubleSpinBox()
        threshold_percent.setRange(1.0, 100.0)
        threshold_percent.setDecimals(1)
        threshold_percent.setSingleStep(1.0)
        threshold_percent.setSuffix("%")
        threshold_percent.setFixedWidth(82)
        threshold_help_text = self.catalog.render("scan.management.grade_help")
        threshold_mode.setToolTip(threshold_help_text)
        threshold_percent.setToolTip(threshold_mode.toolTip())
        threshold_percent.setValue(float(module.get("threshold_percent", 50.0)))
        role_scope = _combo(ROLE_SCOPE_OPTIONS, module.get("role_scope"), 110)
        quality_scope = _combo(QUALITY_SCOPE_OPTIONS, module.get("quality_scope"), 130)
        type_scope = _combo(TYPE_SCOPE_OPTIONS, module.get("type_scope"), 130)
        on_locked = _combo(STATE_ACTION_OPTIONS, module.get("on_locked"), 130)
        on_discarded = _combo(STATE_ACTION_OPTIONS, module.get("on_discarded"), 130)
        self._range_values[key] = {
            "shape_ids": self._selected_or_default(module.get("shape_ids"), self._default_shape_ids()),
            "set_names": self._selected_or_default(module.get("set_names"), self._default_set_names()),
        }
        type_range_row = QWidget()
        type_range_layout = QHBoxLayout(type_range_row)
        type_range_layout.setContentsMargins(0, 0, 0, 0)
        type_range_layout.setSpacing(8)
        type_range_summary = QLabel()
        type_range_summary.setWordWrap(False)
        type_range_summary.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        type_range_button = QPushButton("选择")
        type_range_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        type_range_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:3px 12px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        type_range_button.clicked.connect(
            lambda _checked=False, module_key=key: self._open_type_range_dialog(module_key)
        )
        type_range_layout.addWidget(type_range_summary, 1)
        type_range_layout.addWidget(type_range_button)
        role_scope_row = QWidget()
        role_scope_layout = QHBoxLayout(role_scope_row)
        role_scope_layout.setContentsMargins(0, 0, 0, 0)
        role_scope_layout.setSpacing(8)
        role_scope_summary = QLabel()
        role_scope_button = QPushButton("选择")
        role_scope_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:3px 10px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        role_scope_button.clicked.connect(
            lambda _checked=False, module_key=key: self._open_role_scope_dialog(module_key)
        )
        role_scope_layout.addWidget(role_scope)
        role_scope_layout.addWidget(role_scope_summary, 1)
        role_scope_layout.addWidget(role_scope_button)
        threshold_row = QWidget()
        threshold_layout = QHBoxLayout(threshold_row)
        threshold_layout.setContentsMargins(0, 0, 0, 0)
        threshold_layout.setSpacing(8)
        threshold_layout.addWidget(threshold_mode)
        threshold_layout.addWidget(grade_combo)
        threshold_layout.addWidget(threshold_percent)
        threshold_help_button = QPushButton("?")
        threshold_help_button.setObjectName("btnHelp")
        threshold_help_button.setFixedWidth(24)
        threshold_help_button.setToolTip(threshold_help_text)
        threshold_help_button.clicked.connect(
            lambda: QMessageBox.information(self, "评分等级说明", threshold_help_text)
        )
        threshold_layout.addWidget(threshold_help_button)
        threshold_layout.addStretch()
        form.addRow(grade_label, threshold_row)
        form.addRow("角色范围", role_scope_row)
        form.addRow("品质范围", quality_scope)
        form.addRow("处理类别", type_scope)
        form.addRow("类型范围", type_range_row)
        form.addRow("遇到锁定", on_locked)
        form.addRow("遇到弃置", on_discarded)
        outer.addLayout(form)
        self._widgets[key] = {
            "enabled": enabled,
            "grade": grade_combo,
            "threshold_mode": threshold_mode,
            "threshold_percent": threshold_percent,
            "role_scope": role_scope,
            "role_scope_summary": role_scope_summary,
            "role_scope_button": role_scope_button,
            "quality_scope": quality_scope,
            "type_scope": type_scope,
            "type_range_summary": type_range_summary,
            "on_locked": on_locked,
            "on_discarded": on_discarded,
        }
        role_scope.currentIndexChanged.connect(
            lambda _index, module_key=key: self._update_role_scope_summary(module_key)
        )
        self._update_role_scope_summary(key)
        type_scope.currentIndexChanged.connect(
            lambda _index, module_key=key: self._update_type_range_summary(module_key)
        )
        threshold_mode.currentIndexChanged.connect(
            lambda _index, module_key=key: self._update_threshold_widgets(module_key)
        )
        self._update_threshold_widgets(key)
        self._update_type_range_summary(key)
        outer.addStretch()  # 面板填满页高，额外空间留在底部，不拉散已有表单行。
        return panel

    def _update_threshold_widgets(self, key: str) -> None:
        widgets = self._widgets.get(key, {})
        mode = widgets.get("threshold_mode")
        if mode is None:
            return
        is_percent = mode.currentData() == "percent"
        widgets["grade"].setVisible(not is_percent)
        widgets["threshold_percent"].setVisible(is_percent)

    def _update_role_scope_summary(self, key: str) -> None:
        widgets = self._widgets.get(key, {})
        combo = widgets.get("role_scope")
        summary = widgets.get("role_scope_summary")
        button = widgets.get("role_scope_button")
        if combo is None or summary is None or button is None:
            return
        uses_selected = combo.currentData() == "selected"
        count = len(self._selected_character_ids[key])
        summary.setText(f"已选{count}名" if uses_selected else "")
        summary.setStyleSheet("color:#58a6ff" if count else "color:#f85149")
        button.setVisible(uses_selected)

    def _open_role_scope_dialog(self, key: str) -> None:
        dialog = RoleScopeDialog(
            self,
            self._role_options,
            self._selected_character_ids[key],
            scored_roles=getattr(self, "_role_selection_data", ()),
        )
        dialog.setWindowTitle("选择弃置评估角色" if key == "discard" else "选择锁定评估角色")
        if dialog.exec() != QDialog.Accepted:
            return
        self._selected_character_ids[key] = dialog.selected_character_ids()
        self._update_role_scope_summary(key)

    def _default_shape_ids(self) -> list[str]:
        return [shape_id for shape_id, _area in self._shape_options if shape_id not in DEFAULT_EXCLUDED_SHAPE_IDS]

    def _default_set_names(self) -> list[str]:
        return [set_name for set_name in self._set_options if set_name not in DEFAULT_EXCLUDED_SET_NAMES]

    def _selected_or_default(self, values: list[str] | None, defaults: list[str]) -> list[str]:
        if values is None:
            return list(defaults)
        return [str(value) for value in values if str(value)]

    def _type_range_summary(self, key: str) -> str:
        values = self._range_values[key]
        return type_range_summary(
            self._widgets[key]["type_scope"].currentData(),
            len(values["shape_ids"]),
            len(self._shape_options),
            len(values["set_names"]),
            len(self._set_options),
        )

    def _update_type_range_summary(self, key: str) -> None:
        label = self._widgets[key].get("type_range_summary")
        if label is not None:
            label.setText(self._type_range_summary(key))

    def _open_type_range_dialog(self, key: str) -> None:
        values = self._range_values[key]
        dialog = TypeRangeDialog(
            self,
            self._shape_options,
            self._set_options,
            values["shape_ids"],
            values["set_names"],
            type_scope=self._widgets[key]["type_scope"].currentData(),
        )
        if dialog.exec() != QDialog.Accepted:
            return
        shape_ids, set_names = dialog.selected_values()
        self._range_values[key] = {"shape_ids": shape_ids, "set_names": set_names}
        self._update_type_range_summary(key)
