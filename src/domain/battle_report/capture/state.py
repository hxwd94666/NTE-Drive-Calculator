# 保存采集与双路对照的本次状态，不建立永久战报配对。
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from src.domain.battle_report.capture.summary import BattleSummary


@dataclass(frozen=True, slots=True)
class BattleCaptureState:
    phase: str
    message: str
    running: bool
    summary: BattleSummary | None = None
    error: str | None = None
    error_code: str | None = None
    persistence_status: str = "not_requested"
    battle_record_id: int | None = None
    retention_kind: Literal["auto", "manual"] | None = None
    end_reason: str | None = None


EMPTY_BATTLE_CAPTURE_STATE = BattleCaptureState(
    phase="stopped",
    message="尚未开始战报采集。",
    running=False,
)


@dataclass(frozen=True, slots=True)
class BattleCaptureComparisonState:
    native: BattleCaptureState
    packet: BattleCaptureState
    finished: bool = False
    interrupted: bool = False
    stop_requested: bool = False
