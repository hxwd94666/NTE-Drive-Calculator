# 定义练度统计的不可变行数据，区分原生观测、配置或模板、未知与不可评分。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class PracticeValue:
    text: str = '—'
    configured: bool = False


@dataclass(frozen=True, slots=True)
class PracticeEntry:
    character_id: int
    name: str
    level: PracticeValue = PracticeValue()
    awakening: PracticeValue = PracticeValue()
    heart: PracticeValue = PracticeValue()
    skills: tuple[PracticeValue, ...] = ()
    avatar: Path | None = None
    element_icon: Path | None = None
    fork_name: str = ''
    fork_refinement: PracticeValue = PracticeValue()
    fork_icon: Path | None = None
    score: float | None = None
    grade: str = ''
    active_set_count: int | None = None


def sort_practice_entries(entries: tuple[PracticeEntry, ...]) -> tuple[PracticeEntry, ...]:
    def number(value: PracticeValue) -> int:
        return int(value.text) if value.text.isdigit() else -1
    return tuple(sorted(entries, key=lambda row: (
        row.score is None, -(row.score if row.score is not None else 0),
        -number(row.level), -number(row.awakening), row.character_id,
    )))


@dataclass(frozen=True, slots=True)
class PracticeSharePanel:
    entries: tuple[PracticeEntry, ...]
    notes: tuple[str, ...] = (
        '统计范围按点击时工作模式冻结；＊为账号配置或低风险模板，非游戏实测；—为缺失，全未知行不展示。',
        '好感仅显示已知具体等级，旧十级开关不反推等级；评分沿用完整游戏配装，方案不完整显示 —。',
        '套装显示卡带对应效果的 4／2／0 件激活门槛，非驱动总数；数据不足显示 —。',
    )
