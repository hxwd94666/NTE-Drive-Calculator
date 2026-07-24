# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtWidgets import QHeaderView

from src.app import runtime
from src.app.theme import themed_style
from src.domain.stat_catalog import StatCatalog
from src.features.allocation import results_view as legacy_results
from src.features.inventory.warehouse import WarehouseResultCard, warehouse_item_view
from src.services.official_role_page_service import (
    calculate_official_role_damage_breakdown,
    calculate_official_role_equipment_gain,
    calculate_official_role_item_gain,
    calculate_official_role_margins,
    load_official_role_detail,
    load_official_role_index,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
    save_official_role_tab_order,
)
from src.services.character_weight_service import save_account_character_weights
from src.services.official_equipment_bonus_service import calculate_official_equipment_stats
from src.services.sqlite_allocation_inventory import (
    AllocationInventoryProjectionError,
    legacy_shape_id,
)
from src.storage.sqlite.user_data_dao import UserDataDao
from src.ui.equipment_replacement_dialog import (
    EquipmentReplacementCard,
    show_equipment_replacement_dialog,
)
from src.ui.persistent_tab_order import bind_persistent_tab_order
from src.ui.widgets import (
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    match_pinyin,
)
from .role_calculation import (
    _attribute_name,
    _calculation_detail,
    _clear_layout,
    _mark_dirty,
    _refresh_role_calculations,
)

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes"]

_WEIGHT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"),
    ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"),
    ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"),
    ("防御力", "DefAdd"),
    ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"),
    ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
)
_WEIGHT_LABEL_BY_PROPERTY = {
    property_id: label for label, property_id in _WEIGHT_PROPERTY_CHOICES
}


from . import role_calculation as _calculation
for _module in (_calculation,):
    for _name, _value in vars(_module).items():
        if callable(_value) and not _name.startswith("__"):
            globals().setdefault(_name, _value)

def _build_base_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    character = detail["character"]
    profile = detail["profile"]
    growth_rows = detail["growth_rows"]
    group = QGroupBox("基础加成")
    group.setObjectName("officialRoleBaseGroup")
    group.setStyleSheet("QGroupBox{font-weight:bold;}")
    layout = QVBoxLayout(group)
    content = QHBoxLayout()
    content.setSpacing(16)

    left = QWidget()
    left.setMinimumWidth(132)
    left.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
    left_layout = QVBoxLayout(left)
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(8)
    left_layout.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
    icon_path = detail.get("icon_path")
    if icon_path:
        pixmap = QPixmap(str(icon_path))
        if not pixmap.isNull():
            avatar = QLabel()
            avatar.setObjectName("officialRoleBaseAvatar")
            avatar.setFixedSize(96, 96)
            avatar.setScaledContents(True)
            avatar.setPixmap(pixmap)
            left_layout.addWidget(avatar, alignment=Qt.AlignHCenter)
    role_name = QLabel(str(character.get("name_zh") or character_id))
    role_name.setAlignment(Qt.AlignHCenter)
    role_name.setStyleSheet("font-weight:bold;color:#58a6ff;")
    left_layout.addWidget(role_name)
    growth_combo = NoWheelSpinBox()
    growth_combo.setRange(
        min(int(row["level"]) for row in growth_rows),
        max(int(row["level"]) for row in growth_rows),
    )
    growth_combo.setValue(int(profile["character_level"]))
    growth_combo.setButtonSymbols(QSpinBox.NoButtons)
    level_row = QHBoxLayout()
    level_row.setSpacing(6)
    level_label = QLabel("等级:")
    level_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    level_row.addWidget(level_label)
    growth_combo.setFixedWidth(72)
    level_row.addWidget(growth_combo)
    help_button = QPushButton("?")
    help_button.setObjectName("btnHelp")
    help_button.setFixedSize(22, 22)
    help_button.setStyleSheet(
        "QPushButton#btnHelp{background:#58a6ff;color:white;border-radius:8px;font-weight:bold;"
        "font-size:10px;border:none;padding:0}QPushButton#btnHelp:hover{background:#1f6feb}"
    )
    level_row.addWidget(help_button)
    left_layout.addLayout(level_row)
    left_layout.addStretch()
    content.addWidget(left)

    right = QWidget()
    right_layout = QVBoxLayout(right)
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(8)
    awakening = NoWheelSpinBox()
    awakening.setRange(0, 6)
    awakening.setValue(int(profile["awakening_level"]))
    skill_combo = NoWheelComboBox()
    for skill in detail["skills"]:
        skill_combo.addItem(str(skill["skill_id"]), skill["skill_id"])
    skill_index = skill_combo.findData(profile.get("selected_skill_id"))
    skill_combo.setCurrentIndex(skill_index if skill_index >= 0 else 0)

    stats_grid = QGridLayout()
    stats_grid.setHorizontalSpacing(14)
    stats_grid.setVerticalSpacing(8)
    stat_values = {}
    stat_specs = (
        ("生命白值", "hp_base"),
        ("攻击力白值", "atk_base"),
        ("防御力白值", "def_base"),
        ("暴击率%", "crit_rate"),
        ("暴击伤害%", "crit_damage"),
    )
    for stat_index, (label_text, key) in enumerate(stat_specs):
        grid_row = stat_index // 2
        grid_column = (stat_index % 2) * 2
        label = QLabel(label_text)
        label.setMinimumWidth(92)
        spin = NoWheelDoubleSpinBox()
        spin.setRange(-999999, 999999)
        spin.setDecimals(2)
        spin.setReadOnly(True)
        spin.setButtonSymbols(QDoubleSpinBox.NoButtons)
        spin.setMinimumWidth(110)
        stat_values[key] = spin
        stats_grid.addWidget(label, grid_row, grid_column)
        stats_grid.addWidget(spin, grid_row, grid_column + 1)
    stats_grid.setColumnStretch(1, 1)
    stats_grid.setColumnStretch(3, 1)
    right_layout.addLayout(stats_grid)
    content.addWidget(right, 1)
    layout.addLayout(content)

    def update_stats() -> None:
        level = int(growth_combo.value())
        rows_for_level = [
            row for row in growth_rows if int(row["level"]) == level
        ]
        selected = max(
            rows_for_level,
            key=lambda row: int(row.get("breakthrough_stage") or 0),
            default={},
        )
        stat_values["hp_base"].setValue(float(selected.get("hp_base") or 0))
        stat_values["atk_base"].setValue(float(selected.get("atk_base") or 0))
        stat_values["def_base"].setValue(float(selected.get("def_base") or 0))
        stat_values["crit_rate"].setValue(5.0)
        stat_values["crit_damage"].setValue(50.0)

    update_stats()
    growth_combo.valueChanged.connect(update_stats)

    pointer_dialog = QDialog(window)
    pointer_dialog.setWindowTitle(f"{character.get('name_zh') or character_id} - 养成指针")
    pointer_dialog.resize(520, 240)
    pointer_layout = QVBoxLayout(pointer_dialog)
    pointer_form = QFormLayout()
    pointer_form.addRow("觉醒等级", awakening)
    pointer_form.addRow("直伤技能", skill_combo)
    skill_level = NoWheelSpinBox()
    skill_level.setMinimum(1)
    pointer_form.addRow("技能等级", skill_level)
    pointer_layout.addLayout(pointer_form)
    pointer_note = QLabel("角色等级与突破在主页面左侧选择；其余技能等级会继续保留在账号数据库中。")
    pointer_note.setWordWrap(True)
    pointer_layout.addWidget(pointer_note)
    pointer_close = QPushButton("关闭")
    pointer_close.clicked.connect(pointer_dialog.accept)
    pointer_layout.addWidget(pointer_close)
    help_button.setToolTip("编辑觉醒和直伤技能")
    help_button.clicked.connect(pointer_dialog.exec)

    separator = QFrame()
    separator.setFrameShape(QFrame.HLine)
    separator.setFrameShadow(QFrame.Sunken)
    separator.setStyleSheet(themed_style("background-color:#30363d;max-height:1px"))
    layout.addWidget(separator)

    skills_by_id = {str(skill["skill_id"]): skill for skill in detail["skills"]}
    skill_levels = {str(key): int(value) for key, value in (profile.get("skill_levels") or {}).items()}
    skill_state = {"current": str(skill_combo.currentData() or "")}

    def refresh_skill_level() -> None:
        skill_id = str(skill_combo.currentData() or "")
        skill = skills_by_id.get(skill_id, {})
        levels = [int(row["level"]) for row in skill.get("levels") or ()]
        maximum = max(levels) if levels else 1
        skill_level.blockSignals(True)
        skill_level.setRange(1, maximum)
        skill_level.setValue(int(skill_levels.get(skill_id, maximum)))
        skill_level.blockSignals(False)
        skill_state["current"] = skill_id

    def commit_skill_level(value: int) -> None:
        skill_levels[skill_state["current"]] = int(value)
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    refresh_skill_level()
    skill_combo.currentIndexChanged.connect(refresh_skill_level)
    skill_level.valueChanged.connect(commit_skill_level)

    editor.update({
        "growth": growth_combo,
        "growth_rows": growth_rows,
        "awakening": awakening,
        "selected_skill": skill_combo,
        "skill_levels": skill_levels,
    })

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    for widget in (growth_combo, awakening, skill_combo):
        signal = getattr(widget, "currentIndexChanged", None) or widget.valueChanged
        signal.connect(mark_and_refresh)
    return group


def _fork_stats(detail: dict, fork_id, level: int) -> dict[str, float]:
    fork = next((item for item in detail["forks"] if item.get("fork_id") == fork_id), None)
    if not fork:
        return {}
    upgrades = list(fork.get("upgrade_levels") or ())
    upgrade = min(upgrades, key=lambda row: abs(int(row.get("level") or 0) - level)) if upgrades else None
    breakthroughs = [
        row for row in fork.get("breakthroughs") or ()
        if int(row.get("max_fork_level") or 0) <= level
    ]
    breakthrough = max(breakthroughs, key=lambda row: int(row.get("stage") or 0)) if breakthroughs else None
    totals = {}
    for row in (upgrade, breakthrough):
        for modifier in (row or {}).get("modifiers") or ():
            property_id = str(modifier.get("property_id") or "")
            totals[property_id] = totals.get(property_id, 0.0) + float(modifier.get("value") or 0.0)
    return totals


def _display_property_value(detail: dict, property_id: str, value: float) -> str:
    attribute = detail.get("attributes", {}).get(property_id, {})
    if attribute.get("show_percent"):
        return f"+{value * 100:.2f}%".replace(".00%", "%")
    return f"+{value:.2f}".rstrip("0").rstrip(".")


def _fork_skill_description(star: dict) -> str:
    """Render official refinement placeholders with the selected level's curve values."""

    description = str(star.get("description_zh") or "")
    for parameter in star.get("parameters") or ():
        value = parameter.get("value")
        if value is None:
            continue
        number = float(value) * (100.0 if parameter.get("is_percent") else 1.0)
        shown = f"{number:.6f}".rstrip("0").rstrip(".")
        if parameter.get("is_percent"):
            shown += "%"
        description = description.replace(
            "{" + str(int(parameter.get("ordinal") or 0)) + "}",
            shown,
        )
    return description.replace("<lv>", "").replace("</>", "")


def _build_fork_group(window, character_id: int, detail: dict, editor: dict) -> QGroupBox:
    character = detail["character"]
    profile = detail["profile"]
    group = QGroupBox("弧盘加成")
    group.setObjectName("officialRoleForkGroup")
    layout = QVBoxLayout(group)
    identity = QHBoxLayout()
    identity.addWidget(QLabel("名称:"))
    fork_combo = NoWheelComboBox()
    fork_combo.setMaxVisibleItems(10)
    fork_combo.addItem("未装备弧盘", None)
    for fork in detail["forks"]:
        exclusive = str(character_id) in {str(value) for value in fork.get("exclusive_character_ids") or []}
        suffix = "（专属）" if exclusive else "（常驻同类型）"
        fork_combo.addItem(f"{fork.get('name_zh') or fork['fork_id']} {suffix}", fork["fork_id"])
    fork_index = fork_combo.findData(profile.get("fork_id"))
    fork_combo.setCurrentIndex(fork_index if fork_index >= 0 else 0)
    identity.addWidget(fork_combo, 1)
    fork_level = NoWheelSpinBox()
    fork_level.setRange(1, 80)
    fork_level.setValue(int(profile.get("fork_level") or 80))
    identity.addWidget(QLabel("等级:"))
    identity.addWidget(fork_level)
    refinement = NoWheelComboBox()
    refinement.setMaxVisibleItems(5)
    for level in range(1, 6):
        refinement.addItem(str(level), level)
    refinement_index = refinement.findData(int(profile.get("fork_refinement_level") or 1))
    refinement.setCurrentIndex(refinement_index if refinement_index >= 0 else 0)
    identity.addWidget(QLabel("精炼:"))
    identity.addWidget(refinement)
    margin_label = QLabel("直伤收益: --")
    margin_label.setStyleSheet("color:#ffaa00;font-weight:bold;font-size:13px;")
    identity.addWidget(margin_label)
    layout.addLayout(identity)
    base_label = QLabel("基础加成：")
    base_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(base_label)
    stats_widget = QWidget()
    stats_layout = QVBoxLayout(stats_widget)
    stats_layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(stats_widget)
    effect_label = QLabel("技能描述：")
    effect_label.setStyleSheet("font-weight:bold;color:#58a6ff;")
    layout.addWidget(effect_label)
    effect_text = QLabel()
    effect_text.setWordWrap(True)
    effect_text.setMinimumHeight(72)
    layout.addWidget(effect_text)

    def refresh_fork_summary() -> None:
        _clear_layout(stats_layout)
        fork_id = fork_combo.currentData()
        level = fork_level.value()
        stats = _fork_stats(detail, fork_id, level)
        if not stats:
            stats_layout.addWidget(QLabel("未装备弧盘"))
        for property_id, value in stats.items():
            row = QHBoxLayout()
            row.addWidget(QLabel(_attribute_name(detail, property_id)))
            row.addStretch()
            shown = QLabel(_display_property_value(detail, property_id, value))
            shown.setStyleSheet("color:#58a6ff;font-weight:700;")
            row.addWidget(shown)
            stats_layout.addLayout(row)
        context_key = str(editor.get("equipment_context_key") or "current")
        calculation_detail = _calculation_detail(detail, editor)
        with_fork = {
            **calculation_detail,
            "profile": {
                **calculation_detail["profile"], "fork_id": fork_id, "fork_level": level,
            },
        }
        without_fork = {
            **calculation_detail,
            "profile": {**calculation_detail["profile"], "fork_id": None},
        }
        current = calculate_official_role_margins(with_fork, context_key)
        baseline = calculate_official_role_margins(without_fork, context_key)
        if current and baseline and baseline["damage"] > 0:
            gain = (current["damage"] / baseline["damage"] - 1.0) * 100.0
            margin_label.setText(f"直伤收益: {gain:+.2f}%")
        else:
            margin_label.setText("直伤收益: --")
        fork = next((item for item in detail["forks"] if item.get("fork_id") == fork_id), None)
        star_rows = list((fork or {}).get("star_levels") or ())
        star = next(
            (row for row in star_rows if int(row.get("star_level") or 0) == refinement.currentData()),
            star_rows[0] if star_rows else None,
        )
        if star:
            description = _fork_skill_description(star)
            effect_text.setText(f"{star.get('title_zh') or ''}\n{description}".strip())
        else:
            effect_text.setText("暂无官方精炼说明。")

    fork_combo.currentIndexChanged.connect(refresh_fork_summary)
    fork_level.valueChanged.connect(refresh_fork_summary)
    refinement.currentIndexChanged.connect(refresh_fork_summary)
    refresh_fork_summary()
    editor.update({"fork": fork_combo, "fork_level": fork_level, "refinement": refinement})

    def mark_and_refresh(*_args) -> None:
        _mark_dirty(window, character_id)
        _refresh_role_calculations(editor)

    for widget in (fork_combo, fork_level, refinement):
        signal = getattr(widget, "currentIndexChanged", None) or widget.valueChanged
        signal.connect(mark_and_refresh)
    return group

