# 解析并补全满级卡带主词条数值。
"""Resolve card main-stat values at their calculation level."""

from __future__ import annotations

from functools import lru_cache
from typing import Mapping

from src.domain.stat_catalog import StatCatalog
from src.integrations.bundled_resources import bundled_config_dir


_QUALITY_COEFFICIENTS = {
    "orange": 1.0,
    "gold": 1.0,
    "purple": 0.8,
    "blue": 0.6,
}


@lru_cache(maxsize=1)
def max_level_tape_main_values() -> dict[str, float]:
    """Return the packaged gold-card maximum values keyed by display stat."""

    catalog = StatCatalog.from_config_dir(bundled_config_dir())
    return {name: float(value) for name, value in catalog.tape_main_values.items()}


def full_level_tape_main_value(
    main_stat: str,
    quality: str | None,
    *,
    values: Mapping[str, float] | None = None,
) -> float | None:
    """Return the configured full-level main value for a card quality.

    Inventory snapshots can report a level-one card value.  Allocation treats
    every card main as level-capped, so this helper intentionally ignores that
    transient snapshot value and derives the displayed/calculated value from
    the static card catalogue.
    """

    base = (values or max_level_tape_main_values()).get(str(main_stat or ""))
    if base is None:
        return None
    coefficient = _QUALITY_COEFFICIENTS.get(str(quality or "").casefold(), 1.0)
    return round(float(base) * coefficient, 6)
