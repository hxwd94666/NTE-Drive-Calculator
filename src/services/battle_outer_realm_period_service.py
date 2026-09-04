# 按冻结的战报发生时间解析大陆服轨外期数并约束环境候选。
"""Outer-realm rotation selection independent from catalog build order."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from src.domain.battle_encounter import BattleEncounterCandidate


_MAINLAND_TIMEZONE = timezone(timedelta(hours=8))


@dataclass(frozen=True, slots=True)
class BattleOuterRealmPeriod:
    config_id: str
    season_name: str
    starts_at_mainland: datetime
    ends_at_mainland: datetime
    occurred_at_mainland: datetime

    @property
    def display_label(self) -> str:
        name = self.season_name or self.config_id
        start = self.starts_at_mainland.strftime("%m-%d")
        end = self.ends_at_mainland.strftime("%m-%d")
        return f"{name}（{start}—{end}）"

    @property
    def inference_basis(self) -> str:
        occurred = self.occurred_at_mainland.strftime("%Y-%m-%d %H:%M:%S")
        start = self.starts_at_mainland.strftime("%Y-%m-%d %H:%M:%S")
        end = self.ends_at_mainland.strftime("%Y-%m-%d %H:%M:%S")
        return (
            f"战报发生时间 {occurred}（大陆服）唯一命中轨外期数"
            f" {self.display_label}，正式生效区间为 {start}—{end}"
        )


def _utc_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(_MAINLAND_TIMEZONE).replace(tzinfo=None)


def _mainland_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).replace(tzinfo=None)
    except ValueError:
        return None


def _season_name(config: Mapping[str, object]) -> str:
    buff = config.get("season_buff")
    if not isinstance(buff, Mapping):
        return ""
    return str(buff.get("season_name_zh") or "").strip()


def period_label_for_config(config: Mapping[str, object]) -> str:
    """Build a player-facing season label even outside automatic inference."""

    config_id = str(config.get("level_config_id") or "").strip()
    start = _mainland_datetime(config.get("starts_at_mainland"))
    end = _mainland_datetime(config.get("ends_at_mainland"))
    name = _season_name(config) or config_id
    if start is None or end is None:
        return name
    return f"{name}（{start:%m-%d}—{end:%m-%d}）"


def resolve_outer_realm_period(
    configs: Sequence[Mapping[str, object]],
    battle_occurred_at_utc: object,
) -> BattleOuterRealmPeriod | None:
    """Resolve exactly one formal mainland rotation for a frozen battle time."""

    occurred = _utc_datetime(battle_occurred_at_utc)
    if occurred is None:
        return None
    matched = []
    for config in configs:
        config_id = str(config.get("level_config_id") or "").strip()
        start = _mainland_datetime(config.get("starts_at_mainland"))
        end = _mainland_datetime(config.get("ends_at_mainland"))
        if config_id and start is not None and end is not None and start <= occurred <= end:
            matched.append(BattleOuterRealmPeriod(
                config_id=config_id,
                season_name=_season_name(config),
                starts_at_mainland=start,
                ends_at_mainland=end,
                occurred_at_mainland=occurred,
            ))
    return matched[0] if len(matched) == 1 else None


def filter_candidates_for_period(
    candidates: Sequence[BattleEncounterCandidate],
    period: BattleOuterRealmPeriod | None,
) -> tuple[BattleEncounterCandidate, ...]:
    """Keep non-outer candidates and only the time-selected outer configuration."""

    if period is None:
        return tuple(candidates)
    prefix = f"{period.config_id}|"
    return tuple(
        candidate
        for candidate in candidates
        if candidate.environment_kind != "outer_realm"
        or candidate.environment_ref.startswith(prefix)
    )


__all__ = [
    "BattleOuterRealmPeriod",
    "filter_candidates_for_period",
    "period_label_for_config",
    "resolve_outer_realm_period",
]
