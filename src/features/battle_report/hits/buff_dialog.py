# 分开展示逐击原生 Buff 采样与规则推断的结构化加成详情。
"""Reusable modeless dialog for observed effects and inferred Buff projection."""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.text_catalog import resolve_text_catalog
from collections.abc import Sequence

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QWidget

from src.services.battle_report.native.evidence_rendering import render_field_evidence, render_native_evidence
from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.replay.models import BattleHitReplayResult
from src.domain.battle_report.buffs.models import BattleInferredBuffInterval
from src.services.battle_report.buffs.hit_explanation import (
    BattleHitBuffExplanationService,
)
from .inspection_dialog import HitInspectionDialog


class BattleHitBuffDialog(HitInspectionDialog):
    """Keep one modeless dialog while the user inspects adjacent hit rows."""

    def __init__(self, parent: QWidget | None = None, *, game_ui_asset_root=None, text_catalog=None) -> None:
        super().__init__(parent, game_ui_asset_root=game_ui_asset_root)
        self.text_catalog = resolve_text_catalog(text_catalog)
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.battle_report.init.self_7'))
        self.detail.setObjectName("battleHitBuffDetail")

    def show_for_hit(
        self,
        hit: BattleAnalysisHit,
        intervals: Sequence[BattleInferredBuffInterval],
        *,
        replay: BattleHitReplayResult | None = None,
        projection=None, participant_names=None, target_resolutions=(),
    ) -> None:
        self.setWindowTitle(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.show_for_hit.self', sequence=f'{hit.sequence}', character_name=f'{hit.character_name}'))
        self.inspection.set_hit(hit, replay, projection, participant_names=participant_names, target_resolutions=target_resolutions)
        hp_before = "—" if hit.target_hp_before is None else f"{hit.target_hp_before:,.0f}"
        hp_after = "—" if hit.target_hp_after is None else f"{hit.target_hp_after:,.0f}"
        raw_lines = (
            localized_text(getattr(self, 'text_catalog', None), 'auto.features.battle_report.hits.buff_dialog.show_for_hit.fb90938a2', '事件 ID：{event_id}\n序号：{sequence}\nAbility：{value}\nGE：{value_2}\n组件：{value_3}\n攻击类型：{value_4}\n伤害属性：{value_5}\n目标 ID：{value_6}\nHP：{hp_before} → {hp_after}\nBuff口径：原始逐击归属；公式另按正式来源角色消费属性。\n', event_id=f'{hit.event_id}', sequence=f'{hit.sequence}', value=f"{hit.ability_id or '—'}", value_2=f"{hit.gameplay_effect_id or '—'}", value_3=f"{hit.damage_component or '—'}", value_4=f"{hit.attack_type or '—'}", value_5=f"{hit.damage_attribute or '—'}", value_6=f"{hit.target_id or '—'}", hp_before=f'{hp_before}', hp_after=f'{hp_after}')
        )
        replay_lines = "公式重放：尚未生成"
        if replay is not None:
            factors = "\n".join(
                f"- {row.label}: {row.value:g}（{row.evidence_basis}）"
                for row in replay.factors
            ) or "- 无结构化因子"
            gaps = "\n".join(f"- {row}" for row in replay.missing_evidence)
            replay_lines = (
                localized_text(getattr(self, 'text_catalog', None), 'auto.features.battle_report.hits.buff_dialog.show_for_hit.f5ccaed59', '公式重放：{value}\n暴击判定：{critical_state}\n置信度：{confidence}\n暴击策略：{critical_policy}\n公式类型：{formula_type}\n因子：\n{factors}', value=f"{replay.selected_damage if replay.selected_damage is not None else '—'}", critical_state=f'{replay.critical_state}', confidence=f'{replay.confidence}', critical_policy=f'{replay.critical_policy}', formula_type=f'{replay.formula_type}', factors=f'{factors}')
                + (localized_text(getattr(self, 'text_catalog', None), 'auto.features.battle_report.hits.buff_dialog.show_for_hit.fb20685fe', '\n缺失证据：\n{gaps}', gaps=f'{gaps}') if gaps else "")
            )
        self.detail.setPlainText(
            raw_lines + "\n" + render_field_evidence(hit.field_evidence)
            + "\n" + render_native_evidence(hit.native_evidence)
            + resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.battle_report.show_for_hit.detail') + replay_lines + "\n\n"
            + BattleHitBuffExplanationService.build(hit, intervals, projection=projection,
                                                   allow_projection_fallback=False,
                                                   text_catalog=getattr(self, 'text_catalog', None))
        )
        self.detail.moveCursor(QTextCursor.MoveOperation.Start)
        self.fit_overview()
        self.show()
        self.raise_()
        self.activateWindow()
