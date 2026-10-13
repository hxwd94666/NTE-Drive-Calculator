# 解析并补全满级卡带主词条数值。
"""从发行属性目录投影满级卡带主词条，保留现有品质系数与缺项语义。"""

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
    """发行配置只物化并缓存一次满级金色卡带值，不读取账号或改快照。"""

    catalog = StatCatalog.from_config_dir(bundled_config_dir())
    return {name: float(value) for name, value in catalog.tape_main_values.items()}


def full_level_tape_main_value(
    main_stat: str,
    quality: str | None,
    *,
    values: Mapping[str, float] | None = None,
) -> float | None:
    """以满级目录替代快照暂态等级；缺属性返回 None，未知品质沿既有 1.0 系数，空 values 沿目录回退。"""

    base = (values or max_level_tape_main_values()).get(str(main_stat or ""))
    if base is None:
        return None
    coefficient = _QUALITY_COEFFICIENTS.get(str(quality or "").casefold(), 1.0)
    return round(float(base) * coefficient, 6)
