# 在后台请求中计算人物动态面板与驱动词条收益，返回可丢弃的完整展示结果。
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.services.battle_analysis_progress import BattleAnalysisProgressCallback
from src.domain.battle_counterfactual import BattleMarginalResult
from src.domain.battle_report import BattleAnalysisSnapshot
from src.services.battle_buff_projection_memo import BattleBuffProjectionMemo
from src.services.battle_marginal_calculation_service import BattleMarginalCalculationService


_ELEMENT_PROPERTIES = (
    "DamageUpChaosBase", "DamageUpCosmosBase", "DamageUpIncantationBase",
    "DamageUpLakshanaBase", "DamageUpNatureBase", "DamageUpPsycheBase", "DamageUpPsychicallyBase",
)
_PENETRATION_PROPERTIES = (
    "DamagePenetrateChaos", "DamagePenetrateCosmos", "DamagePenetrateIncantation",
    "DamagePenetrateLakshana", "DamagePenetrateNature", "DamagePenetratePsyche", "DamagePenetratePsychically",
)
CHARACTER_PANEL_DYNAMIC_PROPERTIES = frozenset({
    "CritBase", "CritDamageBase", "DamageUpGeneralBase", "MagBase",
    "AtkUp", "AtkAdd", "HPMaxUp", "HPMaxAdd", "DefUp", "DefAdd",
    "DefIgnore", "UnbalIntensityBase", *_ELEMENT_PROPERTIES, *_PENETRATION_PROPERTIES,
})


def character_panel_marginal_units(drive_units: Mapping[str, float]) -> dict[str, float]:
    """Include zero-delta properties required to project the current dynamic panel."""
    return {
        **{str(key): float(value) for key, value in drive_units.items()},
        **{property_id: 0.0 for property_id in CHARACTER_PANEL_DYNAMIC_PROPERTIES
           if property_id not in drive_units},
    }


@dataclass(frozen=True, slots=True)
class BattleMarginalPanelResult:
    character_id: int
    results: tuple[BattleMarginalResult, ...]
    drive_property_ids: tuple[str, ...]


class BattleMarginalPanelService:
    """DEPRECATED：旧面板计算，仅供离线差分；结果 DTO 继续用于原生解码。"""
    @staticmethod
    def calculate(
        *, analysis: BattleAnalysisSnapshot, character_id: int,
        drive_units: Mapping[str, float], projection_memo: BattleBuffProjectionMemo,
        progress_callback: BattleAnalysisProgressCallback | None = None,
    ) -> BattleMarginalPanelResult:
        return BattleMarginalPanelResult(
            character_id=character_id,
            results=BattleMarginalCalculationService.calculate(
                analysis=analysis, character_id=character_id, edited_values={},
                units=character_panel_marginal_units(drive_units), projection_memo=projection_memo,
                progress_callback=progress_callback,
            ),
            drive_property_ids=tuple(drive_units),
        )
