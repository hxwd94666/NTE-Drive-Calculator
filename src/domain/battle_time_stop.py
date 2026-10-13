# 定义带可选类型证据的战报时停区间。
"""Typed time-stop domain values shared by battle analysis services."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleObservedTimeStopInterval:
    """一次采集时停区间；缺类型掩码保留旧版或压缩后的未知状态。"""

    start_us: int | None
    end_us: int | None
    pause_type_mask: int | None = None
    type_status: Literal["typed", "legacy_unknown", "compacted_unknown"] = (
        "legacy_unknown"
    )
