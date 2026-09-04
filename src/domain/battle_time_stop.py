# 定义带可选类型证据的战报时停区间。
"""Typed time-stop domain values shared by battle analysis services."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class BattleObservedTimeStopInterval:
    """One nte-core pause span with an optional authoritative type mask."""

    start_us: int | None
    end_us: int | None
    pause_type_mask: int | None = None
    type_status: Literal["typed", "legacy_unknown", "compacted_unknown"] = (
        "legacy_unknown"
    )
