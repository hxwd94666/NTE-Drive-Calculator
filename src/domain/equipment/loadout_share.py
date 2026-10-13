# 定义角色配装分享图的不可变展示数据，不包含伤害测算。
"""Read-only values consumed by the loadout image renderer."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ShareStat:
    label: str
    value: str
    highlighted: bool = False
    icon: Path | None = None
    locked: bool = False


@dataclass(frozen=True, slots=True)
class ShareEquipment:
    name: str
    kind: str
    score: float | None
    grade: str
    icon: Path | None
    main_stats: tuple[ShareStat, ...]
    sub_stats: tuple[ShareStat, ...]
    virtual: bool = False


@dataclass(frozen=True, slots=True)
class LoadoutSharePanel:
    role_name: str
    slot_name: str
    score: float
    grade: str
    score_source: str
    cultivation: tuple[ShareStat, ...]
    attributes: tuple[ShareStat, ...]
    skills: tuple[ShareStat, ...]
    fork_name: str
    fork_description: str
    fork_icon: Path | None
    portrait: Path | None
    avatar: Path | None
    equipment: tuple[ShareEquipment, ...]
    notes: tuple[str, ...]
    element_icon: Path | None = None
    fork_stats: tuple[ShareStat, ...] = ()
    fork_level: int | None = None
    fork_refinement: int | None = None
    likeability_level: int | None = None
    loadout_kind: str = "计算配装"
