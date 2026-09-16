# 保存本次双路采集的不可变展示状态，不建立永久战报配对。
from dataclasses import dataclass

from src.domain.battle_report import BattleCaptureState


@dataclass(frozen=True, slots=True)
class BattleCaptureComparisonState:
    native: BattleCaptureState
    packet: BattleCaptureState
    finished: bool = False
    interrupted: bool = False
    stop_requested: bool = False
