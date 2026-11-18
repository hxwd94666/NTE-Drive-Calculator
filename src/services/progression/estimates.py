# 将有版本来源的社区平均产出投影为养成估算动作，不伪装为保底掉落。
from dataclasses import replace
from typing import Iterable

from src.domain.localization.catalog import localized_text
from src.domain.progression.stamina import (
    FarmingStage, MaterialYield, ProgressionStaminaResult, StaminaPlanStatus,
)


# 数值来自公开计算器的 appraisalYield；不复制其计算实现，不推导随机概率。
ESTIMATE_VERSION = "cultivation-estimate-20261106"
ESTIMATE_SOURCE_URL = "https://wiki.mysqil.com/tools/cultivation"
ESTIMATE_SOURCE_SHA256 = "922d4db8fe01cda368d98b9637994ca05b5d761c462724196a23a060b7f56369"
_BOSS_YIELDS = {3: (6, 9075), 4: (6.5, 10800), 5: (7.5, 12000),
                6: (8, 12750), 7: (9, 13125)}


def add_boss_estimates(
    stages: tuple[FarmingStage, ...], item_ids: Iterable[str], identification_level: int,
) -> tuple[FarmingStage, ...]:
    """只补本次需求且缺少正式档位的 Boss；鉴别 0–2 无参数，继续未知。"""
    coefficients = _BOSS_YIELDS.get(identification_level)
    if coefficients is None:
        return stages
    existing = {item.item_id for stage in stages for item in stage.yields}
    quantity, coins = coefficients
    additions = tuple(
        FarmingStage(f"boss-estimate:{item_id}:{identification_level}",
                     localized_text(None, 'auto.services.progression.estimates.add_boss_estimates.f48d37562', '世界 Boss（{item_id}，鉴别 {identification_level}，平均产出估算）', item_id=f'{item_id}', identification_level=f'{identification_level}'),
                     1, identification_level, 60,
                     (MaterialYield(item_id, quantity), MaterialYield("Gold", coins)),
                     ESTIMATE_VERSION)
        for item_id in sorted(set(item_ids) - existing)
    )
    return (*stages, *additions)


def with_material_gaps(result: ProgressionStaminaResult, reasons: Iterable[str]) -> ProgressionStaminaResult:
    """成本资料缺项不能由已知部分的求解成功掩盖，也不丢弃可解释的已知体力。"""
    gaps = tuple(dict.fromkeys((*result.gaps, *reasons)))
    if not gaps:
        return result
    return replace(result, total_stamina=None, gaps=gaps,
                   status=StaminaPlanStatus.PARTIAL if result.runs else StaminaPlanStatus.UNAVAILABLE)
