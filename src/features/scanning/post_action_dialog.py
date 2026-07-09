# 构建全量扫描后状态管理配置弹窗。
"""PySide dialog for post-scan discard/lock settings."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app import runtime
from src.features.scanning.post_actions import (
    DEFAULT_EXCLUDED_SET_NAMES,
    DEFAULT_EXCLUDED_SHAPE_IDS,
    GRADE_ORDER,
    default_post_action_config,
    merge_post_action_config,
    validate_post_action_config,
)
from src.storage.json_store import read_json, write_json
from src.app.theme import themed_style


ROLE_SCOPE_OPTIONS = (("所有角色", "all"), ("所选角色", "selected"))
QUALITY_SCOPE_OPTIONS = (("全部", "all"), ("仅金品质", "gold"), ("仅金紫品质", "gold_purple"))
TYPE_SCOPE_OPTIONS = (("全部", "all"), ("仅驱动", "drive"), ("仅卡带", "tape"))
STATE_ACTION_OPTIONS = (("跳过", "skip"), ("正常处理", "normal"))


def scan_post_action_config_path(user_config_dir: Path) -> Path:
    if user_config_dir is None:
        raise ValueError("user_config_dir is required")
    return Path(user_config_dir) / "scan_post_actions.json"


def load_scan_post_action_config(user_config_dir: Path) -> dict:
    if user_config_dir is None:
        return default_post_action_config()
    path = scan_post_action_config_path(user_config_dir)
    return merge_post_action_config(read_json(path, default=default_post_action_config()))


def save_scan_post_action_config(user_config_dir: Path, config: dict) -> None:
    write_json(scan_post_action_config_path(user_config_dir), merge_post_action_config(config), indent=2)


def _set_combo_data(combo: QComboBox, value: str) -> None:
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return


def _combo(options, value: str, width: int = 130) -> QComboBox:
    combo = QComboBox()
    for label, data in options:
        combo.addItem(label, data)
    _set_combo_data(combo, value)
    combo.setMaximumWidth(width)
    return combo


def _load_drive_shape_options() -> list[tuple[str, int]]:
    config_dir = Path(getattr(runtime, "CONFIG_DIR", Path("config")))
    shapes = (read_json(config_dir / "shapes.json", default={}) or {}).get("shapes", [])
    options = []
    for shape in shapes:
        shape_id = str(shape.get("shape_id") or "")
        area = int(shape.get("area") or 0)
        if shape_id and shape_id != "TAPE_15":
            options.append((shape_id, area))
    return sorted(options, key=lambda item: (item[1], item[0]))


def _load_set_name_options() -> list[str]:
    config_dir = Path(getattr(runtime, "CONFIG_DIR", Path("config")))
    sets = (read_json(config_dir / "sets.json", default={}) or {}).get("sets", {})
    return list(sets.keys())


def _button_style(checked: bool) -> str:
    if checked:
        return themed_style("QPushButton{border:2px solid #2f81f7;background:#10243f;color:#f0f6fc;border-radius:6px;padding:4px}")
    return themed_style("QPushButton{border:1px solid #30363d;background:#161b22;color:#c9d1d9;border-radius:6px;padding:4px}")


class TypeRangeDialog(QDialog):
    def __init__(
        self,
        parent,
        shape_options: list[tuple[str, int]],
        set_options: list[str],
        selected_shape_ids: list[str],
        selected_set_names: list[str],
    ):
        super().__init__(parent)
        self.setWindowTitle("选择类型范围")
        self.setMinimumSize(760, 560)
        self.shape_options = shape_options
        self.set_options = set_options
        self.shape_buttons: list[tuple[QPushButton, str]] = []
        self.set_checks: list[tuple[QCheckBox, str]] = []

        root = QVBoxLayout(self)
        root.setSpacing(12)
        root.addWidget(self._build_shape_section(set(selected_shape_ids)))
        root.addWidget(self._build_set_section(set(selected_set_names)), 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_shape_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("驱动形状"))
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_shapes(True))
        header.addStretch()
        header.addWidget(select_all)
        layout.addLayout(header)

        grouped: dict[int, list[str]] = {2: [], 3: [], 4: []}
        for shape_id, area in self.shape_options:
            grouped.setdefault(area, []).append(shape_id)

        for area in sorted(grouped):
            shape_ids = grouped.get(area, [])
            if not shape_ids:
                continue
            row = QHBoxLayout()
            row.setSpacing(8)
            title = QLabel(f"{area}型")
            title.setFixedWidth(36)
            row.addWidget(title)
            for shape_id in shape_ids:
                button = QPushButton(shape_id)
                button.setCheckable(True)
                button.setChecked(shape_id in selected)
                button.setToolTip(shape_id)
                button.setMinimumSize(84, 54)
                template_dir = Path(getattr(runtime, "TEMPLATE_DIR", Path("config") / "templates"))
                icon_path = template_dir / f"{shape_id}.png"
                if icon_path.exists():
                    button.setIcon(QIcon(str(icon_path)))
                    button.setIconSize(QSize(32, 32))
                button.setStyleSheet(_button_style(button.isChecked()))
                button.toggled.connect(lambda checked, b=button: b.setStyleSheet(_button_style(checked)))
                self.shape_buttons.append((button, shape_id))
                row.addWidget(button)
            row.addStretch()
            layout.addLayout(row)
        return section

    def _build_set_section(self, selected: set[str]) -> QWidget:
        section = QWidget()
        outer = QVBoxLayout(section)
        outer.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        header.addWidget(QLabel("卡带套装"))
        select_all = QPushButton("全选")
        select_all.clicked.connect(lambda: self._set_all_sets(True))
        header.addStretch()
        header.addWidget(select_all)
        outer.addLayout(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        grid = QGridLayout(content)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        for index, set_name in enumerate(self.set_options):
            checkbox = QCheckBox(set_name)
            checkbox.setChecked(set_name in selected)
            self.set_checks.append((checkbox, set_name))
            grid.addWidget(checkbox, index // 2, index % 2)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        return section

    def _set_all_shapes(self, checked: bool) -> None:
        for button, _shape_id in self.shape_buttons:
            button.setChecked(checked)

    def _set_all_sets(self, checked: bool) -> None:
        for checkbox, _set_name in self.set_checks:
            checkbox.setChecked(checked)

    def selected_values(self) -> tuple[list[str], list[str]]:
        shape_ids = [shape_id for button, shape_id in self.shape_buttons if button.isChecked()]
        set_names = [set_name for checkbox, set_name in self.set_checks if checkbox.isChecked()]
        return shape_ids, set_names


class ScanPostActionDialog(QDialog):
    def __init__(self, parent, user_config_dir: Path, selected_roles: list[str] | None = None):
        super().__init__(parent)
        self.user_config_dir = Path(user_config_dir)
        self.selected_roles = selected_roles or []
        self.setWindowTitle("全量扫描管理")
        self.setMinimumWidth(560)
        self.config = load_scan_post_action_config(self.user_config_dir)
        self._widgets = {}
        self._shape_options = _load_drive_shape_options()
        self._set_options = _load_set_name_options()
        self._range_values = {}
        self._build_ui()

    def _style_toggle_button(self, button: QPushButton, checked: bool) -> None:
        button.setText("开启" if checked else "关闭")
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

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setSpacing(12)
        modules = QHBoxLayout()
        modules.setSpacing(14)
        modules.addWidget(self._build_module_panel("discard", "弃置模块", "最高评分低于等于"), 1)
        modules.addWidget(self._build_module_panel("lock", "锁定模块", "最高评分高于等于"), 1)
        root.addLayout(modules)

        footer = QHBoxLayout()
        self.hmt_region_check = QCheckBox("港澳台服")
        self.hmt_region_check.setChecked(self.config.get("server_region") == "hmt")
        self.hmt_region_check.setToolTip("开启后，扫描后弃置/锁定使用港澳台服的十字键左右直控方式。")
        footer.addWidget(self.hmt_region_check)
        footer.addStretch()
        root.addLayout(footer)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _module_help_text(self, key: str) -> str:
        if key == "discard":
            threshold = "最高评分低于等于阈值：适用角色里的最高评分达到该等级或更低时弃置。"
            result = "命中后目标状态是弃置；未命中但当前已弃置时，会取消弃置。"
        else:
            threshold = "最高评分高于等于阈值：适用角色里的最高评分达到该等级或更高时锁定。"
            result = "命中后目标状态是锁定；未命中但当前已锁定时，会取消锁定。"
        return (
            f"{threshold}\n"
            f"{result}\n\n"
            "角色范围：按所有角色或第二步所选角色评分。\n"
            "品质范围：限制品质，范围外不改状态。\n"
            "处理类别：可只处理驱动或卡带。\n"
            "类型范围：驱动按形状过滤，卡带按套装过滤。\n"
            "遇到锁定/弃置：跳过表示保留现状；正常处理表示按本模块结果改成目标状态。"
        )

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
        role_scope = _combo(ROLE_SCOPE_OPTIONS, module.get("role_scope"), 130)
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
        type_range_summary = QLabel(self._type_range_summary(key))
        type_range_button = QPushButton("选择")
        type_range_button.setStyleSheet(
            "QPushButton{background:#1f6feb;color:#fff;border:1px solid #388bfd;"
            "border-radius:6px;padding:3px 12px;font-weight:700;}"
            "QPushButton:hover{background:#388bfd;}"
        )
        type_range_button.clicked.connect(lambda _checked=False, module_key=key: self._open_type_range_dialog(module_key))
        type_range_layout.addWidget(type_range_summary, 1)
        type_range_layout.addWidget(type_range_button)
        form.addRow(grade_label, grade_combo)
        form.addRow("角色范围", role_scope)
        form.addRow("品质范围", quality_scope)
        form.addRow("处理类别", type_scope)
        form.addRow("类型范围", type_range_row)
        form.addRow("遇到锁定", on_locked)
        form.addRow("遇到弃置", on_discarded)
        outer.addLayout(form)
        self._widgets[key] = {
            "enabled": enabled,
            "grade": grade_combo,
            "role_scope": role_scope,
            "quality_scope": quality_scope,
            "type_scope": type_scope,
            "type_range_summary": type_range_summary,
            "on_locked": on_locked,
            "on_discarded": on_discarded,
        }
        return panel

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
        return f"驱动 {len(values['shape_ids'])}/{len(self._shape_options)}，卡带 {len(values['set_names'])}/{len(self._set_options)}"

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
        )
        if dialog.exec() != QDialog.Accepted:
            return
        shape_ids, set_names = dialog.selected_values()
        self._range_values[key] = {"shape_ids": shape_ids, "set_names": set_names}
        self._update_type_range_summary(key)

    def _collect_config(self) -> dict:
        config = default_post_action_config()
        config["server_region"] = "hmt" if self.hmt_region_check.isChecked() else "default"
        for key, widgets in self._widgets.items():
            module = config[key]
            module["enabled"] = widgets["enabled"].isChecked()
            for field in ("grade", "role_scope", "quality_scope", "type_scope", "on_locked", "on_discarded"):
                module[field] = widgets[field].currentData()
            module["shape_ids"] = list(self._range_values[key]["shape_ids"])
            module["set_names"] = list(self._range_values[key]["set_names"])
        return merge_post_action_config(config)

    def _save(self) -> None:
        config = self._collect_config()
        error = validate_post_action_config(config, self.selected_roles)
        if error:
            QMessageBox.warning(self, "配置无效", error)
            return
        save_scan_post_action_config(self.user_config_dir, config)
        self.accept()


def show_scan_post_action_dialog(parent, user_config_dir: Path, selected_roles: list[str] | None = None) -> None:
    dialog = ScanPostActionDialog(parent, user_config_dir, selected_roles)
    dialog.exec()
