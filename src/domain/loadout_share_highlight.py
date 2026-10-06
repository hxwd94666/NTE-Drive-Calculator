# 用冻结权重与正式属性 ID 决定分享图高亮，不参与分数或评级计算。
"""Presentation-only thresholds from the upstream workshop panel."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from src.domain.recommended_weights import WORKSHOP_STAT_PROPERTY_IDS


def share_formal_property(value: str) -> str:
    """Resolve exact legacy aliases at the boundary; never infer from substrings."""
    alias = {"通用伤害增强%": "DamageUpGeneralBase",
             "DamageUpSycheBase": "DamageUpPsycheBase"}
    return alias.get(value, WORKSHOP_STAT_PROPERTY_IDS.get(value, value))


def share_property_id(value: str) -> str:
    return share_formal_property(value).casefold()


def _weights(values: Mapping[str, float]) -> dict[str, float]:
    result = {}
    for key, value in values.items():
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("分享高亮权重包含非有限数值")
        result[share_property_id(str(key))] = number
    return result


@dataclass(frozen=True, slots=True)
class ShareHighlightPolicy:
    main: Mapping[str, float]
    sub: Mapping[str, float]
    effective_ids: frozenset[str] = frozenset()

    @classmethod
    def from_weights(cls, sub: Mapping[str, float], main: Mapping[str, float] | None,
                     effective_ids: Iterable[str] = ()) -> ShareHighlightPolicy:
        # None follows the scorer's legacy fallback; an explicit empty map remains empty.
        return cls(_weights(sub if main is None else main), _weights(sub),
                   frozenset(share_property_id(key) for key in effective_ids))

    def role(self, property_ids: Iterable[str]) -> bool:
        return any(key in self.effective_ids or self.main.get(key, 0) >= .4
                   or self.sub.get(key, 0) >= .4
                   for key in map(share_property_id, property_ids))

    def main_stat(self, property_id: str, *, core: bool) -> bool:
        # Fixed drive-block base attributes do not contribute to main-stat scoring.
        return core and self.main.get(share_property_id(property_id), 0) >= .4

    def sub_stat(self, property_id: str) -> bool:
        key = share_property_id(property_id)
        weight = self.sub.get(key, 0)
        return weight > 0 and (weight >= .4 or key in self.effective_ids)
