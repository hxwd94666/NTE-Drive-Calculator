# 保存逐击原生采样证据的不可变原文，与规则推断结果分离。
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleHitFieldEvidence:
    """分析 Core 给出的逐字段证据判断，与公式拟合结果分离。"""

    critical_state: Literal["critical", "non_critical", "not_applicable", "unknown"]
    critical_source: str
    critical_confidence: str
    damage_attribute_source: str
    damage_attribute_confidence: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleNativeHitEvidence:
    """无损保存采集信封 JSON；原始证据不是公式修正项。"""

    payload_json: str
    static_names: tuple[tuple[str, str], ...] = ()
