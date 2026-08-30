# 争锋赏宴大陆服活动期与正式挑战成员的只读项目注解。
"""Keep public schedule evidence separate from the current packaged stage table."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.services.static_catalog_monster_models import FeastPeriod


@dataclass(frozen=True)
class _FeastPeriodDefinition:
    period_id: str
    display_label: str
    starts_at_mainland: str
    ends_at_mainland: str
    schedule_label: str
    challenge_ids: tuple[str, ...]
    evidence_label: str
    evidence_url: str


_PERIODS = (
    _FeastPeriodDefinition(
        period_id="cn_1_3_20260813",
        display_label="1.3 期",
        starts_at_mainland="2026-08-13T11:00:00",
        ends_at_mainland="2026-09-24T05:59:00",
        schedule_label="2026-08-13 版本更新后—2026-09-24 05:59",
        challenge_ids=(
            "DiyBossStage1", "DiyBossStage2", "DiyBossStage3",
            "DiyBossStage4", "DiyBossStage5", "DiyBossStage6",
            "DiyBossStage7", "DiyBossStage8",
        ),
        evidence_label="《异环》1.3 版本「雾中朔望星回」更新公告",
        evidence_url="https://yh.wanmei.com/news/gamebroad/20260812/263538.html",
    ),
    _FeastPeriodDefinition(
        period_id="cn_1_1_20260612",
        display_label="1.1 往期",
        starts_at_mainland="2026-06-12T10:00:00",
        ends_at_mainland="2026-07-02T05:59:00",
        schedule_label="2026-06-12 10:00—2026-07-02 05:59",
        challenge_ids=(
            "DiyBossStage1", "DiyBossStage111", "DiyBossStage3",
            "DiyBossStage2", "DiyBossStage5", "DiyBossStage6",
            "DiyBossStage4",
        ),
        evidence_label="《异环》1.1 版本「游梦洄廊」更新公告",
        evidence_url="https://yh.wanmei.com/m/news/gamebroad/20260527/262372.html",
    ),
)

_HISTORICAL_BOSS_NAMES = {
    "DiyBossStage111": "随心泥",
}


def feast_periods(mainland_now: datetime) -> tuple[FeastPeriod, ...]:
    """Return current/future periods first and ended periods newest first."""

    periods = tuple(
        FeastPeriod(
            period_id=row.period_id,
            display_label=row.display_label,
            release_state=_release_state(row, mainland_now),
            schedule_label=row.schedule_label,
            challenge_ids=row.challenge_ids,
            evidence_label=row.evidence_label,
            evidence_url=row.evidence_url,
        )
        for row in _PERIODS
    )
    order = {"current": 0, "next": 1, "scheduled": 2, "historical": 3}
    return tuple(sorted(
        periods,
        key=lambda row: (
            order.get(row.release_state, 9),
            -_timestamp(row.period_id),
        ),
    ))


def feast_period(period_id: str, mainland_now: datetime) -> FeastPeriod | None:
    return next(
        (row for row in feast_periods(mainland_now) if row.period_id == period_id),
        None,
    )


def historical_boss_name(stage_id: str) -> str:
    return _HISTORICAL_BOSS_NAMES.get(str(stage_id), "")


def _release_state(row: _FeastPeriodDefinition, mainland_now: datetime) -> str:
    start = datetime.fromisoformat(row.starts_at_mainland)
    end = datetime.fromisoformat(row.ends_at_mainland)
    if start <= mainland_now <= end:
        return "current"
    return "historical" if end < mainland_now else "scheduled"


def _timestamp(period_id: str) -> int:
    tail = period_id.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0
