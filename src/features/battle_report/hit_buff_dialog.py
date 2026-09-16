# 分开展示逐击原生 Buff 采样与规则推断的结构化加成详情。
"""Reusable modeless dialog for observed effects and inferred Buff projection."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QWidget

from src.services.battle_native_evidence_rendering import render_field_evidence, render_native_evidence
from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleHitReplayResult,
    BattleInferredBuffInterval,
)
from src.services.battle_hit_buff_explanation_service import (
    BattleHitBuffExplanationService,
)
from .hit_inspection_dialog import HitInspectionDialog


class BattleHitBuffDialog(HitInspectionDialog):
    """Keep one modeless dialog while the user inspects adjacent hit rows."""

    def __init__(self, parent: QWidget | None = None, *, game_ui_asset_root=None) -> None:
        super().__init__(parent, game_ui_asset_root=game_ui_asset_root)
        self.setWindowTitle("逐击详情")
        self.detail.setObjectName("battleHitBuffDetail")

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
        *,
        replay: BattleHitReplayResult | None = None,
        projection=None, participant_names=None, target_resolutions=(),
    ) -> None:
        self.setWindowTitle(f"逐击详情 · Hit #{hit.sequence} · {hit.character_name}")
        self.inspection.set_hit(hit, replay, projection, participant_names=participant_names, target_resolutions=target_resolutions)
        hp_before = "—" if hit.target_hp_before is None else f"{hit.target_hp_before:,.0f}"
        hp_after = "—" if hit.target_hp_after is None else f"{hit.target_hp_after:,.0f}"
        raw_lines = (
            f"事件 ID：{hit.event_id}\n序号：{hit.sequence}\n"
            f"Ability：{hit.ability_id or '—'}\nGE：{hit.gameplay_effect_id or '—'}\n"
            f"组件：{hit.damage_component or '—'}\n攻击类型：{hit.attack_type or '—'}\n"
            f"伤害属性：{hit.damage_attribute or '—'}\n目标 ID：{hit.target_id or '—'}\n"
            f"HP：{hp_before} → {hp_after}\n"
            "Buff口径：原始逐击归属；公式另按正式来源角色消费属性。\n"
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
            raw_lines + "\n" + render_field_evidence(hit.field_evidence)
            + "\n" + render_native_evidence(hit.native_evidence)
            + "\n\n规则推断与公式重放（与原生采样分开）\n" + replay_lines + "\n\n"
            + BattleHitBuffExplanationService.build(hit, intervals, projection=projection,
                                                   allow_projection_fallback=False)
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)
        self.fit_overview()
        self.show()
        self.raise_()
        self.activateWindow()
