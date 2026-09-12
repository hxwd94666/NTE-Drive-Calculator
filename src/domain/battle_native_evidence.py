# 保存逐击原生采样证据的不可变原文，与规则推断结果分离。
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleHitFieldEvidence:
    """Field decisions supplied by the analysis core, independent of formula fit."""

    critical_state: Literal["critical", "non_critical", "not_applicable", "unknown"]
    critical_source: str
    critical_confidence: str
    damage_attribute_source: str
    damage_attribute_confidence: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BattleNativeHitEvidence:
    """Lossless JSON of the captured native envelope, never a formula modifier."""

    payload_json: str
    static_names: tuple[tuple[str, str], ...] = ()
