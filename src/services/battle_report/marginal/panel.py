# 在后台请求中计算人物动态面板与驱动词条收益，返回可丢弃的完整展示结果。
from __future__ import annotations

from src.domain import battle_page_models

from collections.abc import Mapping

from src.services.battle_report.analysis.progress import BattleAnalysisProgressCallback
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.marginal.calculation import BattleMarginalCalculationService




def character_panel_marginal_units(drive_units: Mapping[str, float]) -> dict[str, float]:
    """Include zero-delta properties required to project the current dynamic panel."""
    return {
        **{str(key): float(value) for key, value in drive_units.items()},
        **{property_id: 0.0 for property_id in battle_page_models.CHARACTER_PANEL_DYNAMIC_PROPERTIES
           if property_id not in drive_units},
    }




class BattleMarginalPanelService:
    """DEPRECATED：旧面板计算，仅供离线差分；结果使用统一领域读模型。"""
    @staticmethod
    def calculate(
        *, analysis: BattleAnalysisSnapshot, character_id: int,
        drive_units: Mapping[str, float], projection_memo: BattleBuffProjectionMemo,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> battle_page_models.BattleMarginalPanelResult:
        return battle_page_models.BattleMarginalPanelResult(
            character_id=character_id,
            results=BattleMarginalCalculationService.calculate(
                analysis=analysis, character_id=character_id, edited_values={},
                units=character_panel_marginal_units(drive_units), projection_memo=projection_memo,
                progress_callback=progress_callback,
            ),
            drive_property_ids=tuple(drive_units),
        )
