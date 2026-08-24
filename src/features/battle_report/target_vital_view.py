# 展示目标原始逐击、最大生命变化与派生结算证据。
"""Target-vital evidence tables for one immutable battle analysis snapshot."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import BattleAnalysisSnapshot
from src.features.battle_report.analysis_components import analysis_table
from src.features.battle_report.target_condition_selector import (
    BattleTargetConditionSelector,
)
from src.ui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox


_RESISTANCE_LABELS = (
    ("normal", "普通抗性"),
    ("chaos", "暗抗"),
    ("cosmos", "光抗"),
    ("incantation", "咒抗"),
    ("lakshana", "相抗"),
    ("nature", "灵抗"),
    ("psyche", "魂抗"),
    ("psychically", "心灵抗性"),
)


def _number(value: float) -> str:
    return f"{value:,.0f}"


def _optional_number(value: float | None) -> str:
    return "—" if value is None else _number(value)


def _time(value_us: int) -> str:
    seconds = max(0, value_us) / 1_000_000.0
    minutes = int(seconds // 60)
    return f"{minutes:02d}:{seconds - minutes * 60:06.3f}"


def _half_label(value: str) -> str:
    return {"upper": "上半", "lower": "下半"}.get(value.casefold(), "全场")


class BattleTargetVitalPanel(QWidget):
    condition_save_requested = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.environment_dialog = QDialog(self)
        self.environment_dialog.setWindowTitle("配置本场战斗环境")
        self.environment_dialog.setModal(True)
        dialog_layout = QVBoxLayout(self.environment_dialog)
        self.condition_selector = BattleTargetConditionSelector(
            self.environment_dialog
        )
        self.condition_selector.preset_changed.connect(self._apply_preset)
        dialog_layout.addWidget(self.condition_selector)
        self._selection_metadata: dict[str, object] = {}
        condition_grid = QGridLayout()
        condition_grid.setHorizontalSpacing(8)
        condition_grid.setVerticalSpacing(8)
        condition_grid.addWidget(QLabel("目标名称"), 0, 0)
        self.target_name_edit = QLineEdit()
        condition_grid.addWidget(self.target_name_edit, 0, 1)
        condition_grid.addWidget(QLabel("敌方等级"), 0, 2)
        self.enemy_level_spin = self._number_spin(1.0, 999.0, decimals=0)
        condition_grid.addWidget(self.enemy_level_spin, 0, 3)
        condition_grid.addWidget(QLabel("场景"), 0, 4)
        self.scene_combo = NoWheelComboBox()
        self.scene_combo.addItem("轨外之境", "outer_realm")
        self.scene_combo.addItem("大世界", "open_world")
        condition_grid.addWidget(self.scene_combo, 0, 5)
        condition_grid.addWidget(QLabel("敌方防御降低"), 0, 6)
        self.defense_reduction_spin = self._percent_spin(-100.0, 100.0)
        condition_grid.addWidget(self.defense_reduction_spin, 0, 7)
        condition_grid.addWidget(QLabel("敌方易伤"), 0, 8)
        self.vulnerability_spin = self._percent_spin(-100.0, 1000.0)
        condition_grid.addWidget(self.vulnerability_spin, 0, 9)
        condition_grid.addWidget(QLabel("实际 DefBase"), 3, 0)
        self.enemy_defense_base_spin = self._number_spin(
            0.0,
            1_000_000_000.0,
            decimals=2,
        )
        self.enemy_defense_base_spin.setSpecialValueText("按等级近似")
        condition_grid.addWidget(self.enemy_defense_base_spin, 3, 1)
        condition_grid.addWidget(QLabel("DefUp"), 3, 2)
        self.enemy_defense_up_spin = self._percent_spin(-100.0, 1000.0)
        condition_grid.addWidget(self.enemy_defense_up_spin, 3, 3)
        condition_grid.addWidget(QLabel("DefAdd"), 3, 4)
        self.enemy_defense_add_spin = self._number_spin(
            -1_000_000_000.0,
            1_000_000_000.0,
            decimals=2,
        )
        condition_grid.addWidget(self.enemy_defense_add_spin, 3, 5)
        condition_grid.addWidget(QLabel("UnbalMax"), 3, 6)
        self.enemy_topple_limit_spin = self._number_spin(
            0.0,
            1_000_000.0,
            decimals=2,
        )
        condition_grid.addWidget(self.enemy_topple_limit_spin, 3, 7)
        self.resistance_spins: dict[str, NoWheelDoubleSpinBox] = {}
        for index, (damage_type, label) in enumerate(_RESISTANCE_LABELS):
            row = 1 + index // 4
            column = (index % 4) * 2
            condition_grid.addWidget(QLabel(f"{label}（战前）"), row, column)
            editor = self._percent_spin(-500.0, 500.0)
            self.resistance_spins[damage_type] = editor
            condition_grid.addWidget(editor, row, column + 1)
        self.save_condition_button = QPushButton("保存环境配置")
        self.save_condition_button.setObjectName("btnPrimary")
        self.save_condition_button.clicked.connect(self._request_condition_save)
        self.save_condition_button.clicked.connect(self.environment_dialog.accept)
        condition_grid.addWidget(self.save_condition_button, 2, 8, 1, 2)
        dialog_layout.addLayout(condition_grid)
        self.condition_note = QLabel()
        self.condition_note.setWordWrap(True)
        self.condition_note.setStyleSheet(
            themed_style("color:#d29922;font-size:12px")
        )
        dialog_layout.addWidget(self.condition_note)
        dialog_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        dialog_buttons.rejected.connect(self.environment_dialog.reject)
        dialog_layout.addWidget(dialog_buttons)
        self.target_table = analysis_table(
            (
                "半场",
                "目标",
                "实例",
                "命中",
                "正式逐击",
                "上限下降",
                "上限结算伤害",
                "描述预计",
                "分析有效伤害",
                "观测耗血",
                "未解释差额",
                "首次 HP",
                "终止 HP",
                "原始最大 HP",
            ),
            190,
            default_widths=(
                70,
                145,
                185,
                70,
                110,
                110,
                130,
                120,
                110,
                110,
                110,
                110,
                120,
                130,
            ),
        )
        layout.addWidget(self.target_table)
        self.event_table = analysis_table(
            (
                "时间",
                "目标",
                "归属",
                "机制",
                "最大 HP 变化",
                "结算前生命比例",
                "有效伤害",
                "证据",
                "置信度",
            ),
            210,
            default_widths=(
                110,
                145,
                110,
                245,
                180,
                150,
                120,
                110,
                150,
            ),
        )
        layout.addWidget(self.event_table)
        self.note = QLabel(
            "生命上限结算 = 变化前同半场、同实例、同旧上限的附近逐击中最小 "
            "HPAfter ÷ 旧 HPMax × HPMax 下降量。正式逐击不改写；"
            "观测耗血 = 分析有效伤害 + 未解释差额。"
        )
        self.note.setWordWrap(True)
        self.note.setStyleSheet(themed_style("color:#d29922;font-size:12px"))
        layout.addWidget(self.note)

    def clear(self) -> None:
        self.target_table.setRowCount(0)
        self.event_table.setRowCount(0)
        self.condition_note.setText("尚未加载敌方条件。")

    def set_catalog(self, catalog: dict[str, object]) -> None:
        self.condition_selector.set_catalog(catalog)

    def open_environment_dialog(self) -> None:
        fit_dialog_to_available_screen(
            self.environment_dialog,
            QSize(1040, 720),
        )
        self.environment_dialog.open()

    def render(
        self,
        analysis: BattleAnalysisSnapshot,
        *,
        projected_time: Callable[[int], int],
    ) -> None:
        condition = analysis.target_condition
        self.condition_selector.render(
            condition,
            detected_environment_kind=getattr(
                analysis,
                "detected_environment_kind",
                "",
            ),
            detected_environment_ref=getattr(
                analysis,
                "detected_environment_ref",
                "",
            ),
            detected_difficulty_id=getattr(
                analysis,
                "detected_environment_difficulty_id",
                None,
            ),
            detected_options=getattr(
                analysis,
                "detected_environment_options",
                (),
            ),
            detected_floor=getattr(
                analysis,
                "detected_outer_realm_floor",
                None,
            ),
        )
        self._selection_metadata = self.condition_selector.current_preset()
        suggested_name = next(
            (
                target.target_name
                for target in analysis.targets
                if target.target_name != "未知目标"
            ),
            "单目标（待确认）",
        )
        self.target_name_edit.setText(
            condition.target_name if condition is not None else suggested_name
        )
        self.enemy_level_spin.setValue(
            condition.enemy_level if condition is not None else 90.0
        )
        scene = condition.scene if condition is not None else "outer_realm"
        self.scene_combo.setCurrentIndex(max(0, self.scene_combo.findData(scene)))
        self.defense_reduction_spin.setValue(
            (condition.defense_reduction if condition is not None else 0.0) * 100.0
        )
        self.vulnerability_spin.setValue(
            (condition.vulnerability if condition is not None else 0.0) * 100.0
        )
        self.enemy_defense_base_spin.setValue(
            condition.enemy_defense_base
            if condition is not None and condition.enemy_defense_base is not None
            else 0.0
        )
        self.enemy_defense_up_spin.setValue(
            (condition.enemy_defense_up if condition is not None else 0.0) * 100.0
        )
        self.enemy_defense_add_spin.setValue(
            condition.enemy_defense_add if condition is not None else 0.0
        )
        self.enemy_topple_limit_spin.setValue(
            condition.enemy_topple_limit if condition is not None else 50.0
        )
        resistances = dict(condition.resistances) if condition is not None else {}
        for damage_type, editor in self.resistance_spins.items():
            editor.setValue(float(resistances.get(damage_type, 0.20)) * 100.0)
        self.condition_note.setText(
            (
                "已保存用户确认的单目标条件；战前抗性包含模式追加/弱点，"
                "不包含战斗中的临时减抗。实际 DefBase 非零时优先使用绑定属性包，"
                "否则才按等级与场景近似；易伤默认 0。"
                if condition is not None
                else "当前战报没有目标实例/怪物 ID。以上战前抗性 20% 仅是"
                "编辑初值，保存前不参与需要敌方参数的计算；不要把战斗中"
                "临时减抗重复填入。"
            )
        )
        self.target_table.setRowCount(len(analysis.targets))
        for row, target in enumerate(analysis.targets):
            values = (
                _half_label(target.scope_half),
                target.target_name,
                (
                    "—（无实例 ID）"
                    if target.target_id == "unknown"
                    else target.target_id
                ),
                f"{target.hits:,}",
                _number(target.damage),
                _number(target.max_hp_reduction),
                _number(target.max_hp_reduction_damage),
                _number(target.estimated_max_hp_reduction_damage),
                _number(target.effective_damage),
                _number(target.observed_hp_loss),
                _number(target.unexplained_hp_delta),
                _optional_number(target.initial_hp),
                _optional_number(target.terminal_hp),
                _optional_number(target.max_hp),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column >= 3:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 10:
                    item.setToolTip(
                        "正数表示观测耗血仍高于已解释伤害；负数表示分析伤害"
                        "高于观测耗血，常见于溢出、回血抵消、并发样本或复合事件。"
                        "该差额只用于闭合账本，不分摊给角色或技能。"
                    )
                self.target_table.setItem(row, column, item)

        events = tuple(
            sorted(
                (*analysis.max_hp_events, *analysis.estimated_max_hp_events),
                key=lambda event: (event.observed_at_us, event.event_id),
            )
        )
        self.event_table.setRowCount(len(events))
        for row, event in enumerate(events):
            values = (
                _time(projected_time(event.observed_at_us)),
                event.target_name,
                event.source_character_name,
                event.mechanic_name,
                f"{_number(event.old_max_hp)} → {_number(event.new_max_hp)}",
                f"{event.hp_ratio_before * 100:.2f}%",
                _number(event.effective_hp_loss),
                (
                    "实测变化"
                    if event.included_in_effective_damage
                    else "描述预计"
                ),
                f"归因{event.attribution_confidence} / 结算{event.calculation_confidence}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in {0, 4, 5, 6}:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if column == 3:
                    item.setToolTip(event.inference_basis)
                self.event_table.setItem(row, column, item)
        identity_note = {
            "instance_scoped": (
                "目标实例 ID 完整：最大生命状态按半场与实例分别维护。正式逐击不改写；"
                "观测耗血、分析有效伤害和未解释差额严格闭合，差额不分摊给角色。"
            ),
            "mixed_guarded": (
                "目标实例 ID 部分缺失：只有可识别实例参与最大生命派生，未知目标行已排除，"
                "避免跨目标串联。"
            ),
            "single_target_assumed": (
                "本记录没有目标实例 ID：当前按单目标假设合并为一行“未知目标”。"
                "若实战存在多个敌人，本页生命上限结算不可作为可靠结果。"
            ),
            "user_confirmed_single_target": (
                "本记录没有正式目标实例 ID；用户已明确只选择一个当前计算对象，"
                "分析投影已把全部对敌逐击绑定到该对象。该身份属于用户证据，"
                "不会写回或冒充 nte-core 原始实例 ID。"
            ),
        }.get(
            analysis.target_identity_mode,
            "没有可用于生命上限派生的正式目标血量样本。",
        )
        estimate_note = (
            f" 描述预计合计 {_number(analysis.estimated_max_hp_reduction_damage)}，"
            "属于低置信弱证据，默认不计入有效伤害。"
            if analysis.estimated_max_hp_events
            else ""
        )
        self.note.setText(identity_note + estimate_note)

    @staticmethod
    def _number_spin(
        minimum: float,
        maximum: float,
        *,
        decimals: int,
    ) -> NoWheelDoubleSpinBox:
        editor = NoWheelDoubleSpinBox()
        editor.setDecimals(decimals)
        editor.setRange(minimum, maximum)
        return editor

    @classmethod
    def _percent_spin(
        cls,
        minimum: float,
        maximum: float,
    ) -> NoWheelDoubleSpinBox:
        editor = cls._number_spin(minimum, maximum, decimals=2)
        editor.setSuffix("%")
        return editor

    def _request_condition_save(self) -> None:
        payload = {
            "target_name": self.target_name_edit.text().strip(),
            "enemy_level": self.enemy_level_spin.value(),
            "scene": self.scene_combo.currentData(),
            "enemy_defense_base": self.enemy_defense_base_spin.value() or None,
            "enemy_defense_up": self.enemy_defense_up_spin.value() / 100.0,
            "enemy_defense_add": self.enemy_defense_add_spin.value(),
            "enemy_topple_limit": self.enemy_topple_limit_spin.value(),
            "defense_reduction": self.defense_reduction_spin.value() / 100.0,
            "vulnerability": self.vulnerability_spin.value() / 100.0,
            "resistances": {
                damage_type: editor.value() / 100.0
                for damage_type, editor in self.resistance_spins.items()
            },
        }
        payload.update({
            key: self._selection_metadata.get(key)
            for key in (
                "environment_kind",
                "environment_ref",
                "selected_target_ids",
                "primary_target_id",
                "difficulty_id",
                "feast_options",
                "witch_buff_id",
                "witch_buff_name_zh",
                "witch_buff_property_id",
                "witch_buff_value",
                "witch_buff_is_percent",
            )
        })
        self.condition_save_requested.emit(payload)

    def _apply_preset(self, preset: object) -> None:
        if not isinstance(preset, dict):
            return
        self._selection_metadata = dict(preset)
        if not preset.get("target_name"):
            return
        self.target_name_edit.setText(str(preset["target_name"]))
        self.enemy_level_spin.setValue(float(preset["enemy_level"]))
        self.scene_combo.setCurrentIndex(
            max(0, self.scene_combo.findData(preset["scene"]))
        )
        self.enemy_defense_base_spin.setValue(
            float(preset.get("enemy_defense_base") or 0.0)
        )
        self.enemy_defense_up_spin.setValue(
            float(preset.get("enemy_defense_up") or 0.0) * 100.0
        )
        self.enemy_defense_add_spin.setValue(
            float(preset.get("enemy_defense_add") or 0.0)
        )
        self.enemy_topple_limit_spin.setValue(
            float(preset.get("enemy_topple_limit") or 50.0)
        )
        resistances = preset.get("resistances") or {}
        for damage_type, editor in self.resistance_spins.items():
            editor.setValue(float(resistances.get(damage_type, 0.0)) * 100.0)
        self.condition_note.setText(
            "已从官方静态目录载入当前对象及属性包；争锋加成已叠加到战前参数。"
            "仍可在下方人工补正，点击保存后才参与逐击重放。"
        )
