# 展示逐击日志中单击“推算 Buff”后的结构化加成详情。
"""Reusable modeless dialog for one hit's inferred Buff projection."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_buff_explanation_service import (
    BattleHitBuffExplanationService,
)
from src.services.skill_name_rendering_service import preferred_battle_damage_name


class BattleHitBuffDialog(QDialog):
    """Keep one modeless dialog while the user inspects adjacent hit rows."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("逐击详情")
        self.setModal(False)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self.title_label = QLabel("逐击推算 Buff")
        self.title_label.setStyleSheet(
            themed_style("color:#58a6ff;font-size:16px;font-weight:700")
        )
        root.addWidget(self.title_label)

        self.detail = QPlainTextEdit()
        self.detail.setObjectName("battleHitBuffDetail")
        self.detail.setReadOnly(True)
        self.detail.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        root.addWidget(self.detail, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.hide)
        root.addWidget(buttons)
        fit_dialog_to_available_screen(self, QSize(980, 760))

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
        *,
        replay: BattleHitReplayResult | None = None,
    ) -> None:
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        self.title_label.setText(f"{hit.character_name} · {damage_name} · 逐击详情")
        hp_before = "—" if hit.target_hp_before is None else f"{hit.target_hp_before:,.0f}"
        hp_after = "—" if hit.target_hp_after is None else f"{hit.target_hp_after:,.0f}"
        raw_lines = (
            f"事件 ID：{hit.event_id}\n序号：{hit.sequence}\n"
            f"Ability：{hit.ability_id or '—'}\nGE：{hit.gameplay_effect_id or '—'}\n"
            f"组件：{hit.damage_component or '—'}\n攻击类型：{hit.attack_type or '—'}\n"
            f"伤害属性：{hit.damage_attribute or '—'}\n目标 ID：{hit.target_id or '—'}\n"
            f"HP：{hp_before} → {hp_after}\n"
        )
        replay_lines = "公式重放：尚未生成"
        if replay is not None:
            factors = "\n".join(
                f"- {row.label}: {row.value:g}（{row.evidence_basis}）"
                for row in replay.factors
            ) or "- 无结构化因子"
            gaps = "\n".join(f"- {row}" for row in replay.missing_evidence)
            replay_lines = (
                f"公式重放：{replay.selected_damage if replay.selected_damage is not None else '—'}\n"
                f"暴击判定：{replay.critical_state}\n置信度：{replay.confidence}\n"
                f"暴击策略：{replay.critical_policy}\n公式类型：{replay.formula_type}\n"
                f"因子：\n{factors}"
                + (f"\n缺失证据：\n{gaps}" if gaps else "")
            )
        self.detail.setPlainText(
            raw_lines + "\n" + replay_lines + "\n\n"
            + BattleHitBuffExplanationService.build(hit, intervals)
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)
        fit_dialog_to_available_screen(self, QSize(980, 760))
        self.show()
        self.raise_()
        self.activateWindow()
