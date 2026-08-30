# 怪物与玩法页面的不可变浏览状态和正式记录键解析。
"""Qt-free browse models and typed-key helpers for the monster page."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import unquote

from src.services.static_catalog_monster_service import CatalogEntry


PLAY_LABELS = {
    "official_illustrated": "大世界图鉴",
    "feast": "争锋赏宴",
    "outer_realm": "当前 / 下一期轨外之境",
    "clone": "材料与养成副本",
    "world_boss": "异象追猎",
    "high_risk": "具有正式怪物池的高危委托",
}
PLAY_COPY = {
    "official_illustrated": "按地区浏览大世界敌人，查看弱点与战斗属性。",
    "feast": "按期选择挑战对象、难度与挑战条件，动态查看敌方强度。",
    "outer_realm": "先看当期和预计期，再按层与半场浏览敌人。",
    "clone": "材料、养成与活动副本，可按难度查看出场敌人。",
    "world_boss": "浏览异象追猎目标和不同等级下的战斗属性。",
    "high_risk": "仅收录各难度已有明确出场敌人的高危委托。",
}


@dataclass(frozen=True, slots=True)
class BrowseCard:
    title: str
    subtitle: str
    badge: str
    icon: Path | None
    action: Callable[[], None] | None
    formal_id: str = ""
    unavailable: bool = False
    category: str = ""
    difficulty: str = ""
    region: str = ""
    period: str = ""


@dataclass(frozen=True, slots=True)
class BrowseSection:
    title: str
    note: str
    cards: tuple[BrowseCard, ...]
    initial_limit: int = 0


@dataclass(frozen=True, slots=True)
class BrowseState:
    title: str
    subtitle: str
    sections: tuple[BrowseSection, ...]


def group_entries(
    entries: Iterable[CatalogEntry], key: Callable[[CatalogEntry], object],
) -> dict[str, tuple[CatalogEntry, ...]]:
    rows: dict[str, list[CatalogEntry]] = defaultdict(list)
    for entry in entries:
        rows[str(key(entry))].append(entry)
    return {name: tuple(values) for name, values in rows.items()}


def key_parts(key: str) -> tuple[str, ...]:
    return tuple(unquote(part) for part in key.split("|"))


def profile_parts(key: str) -> tuple[str, str] | None:
    parts = key_parts(key)
    return (
        (parts[1], parts[2])
        if len(parts) == 3 and parts[0] == "profile_monster"
        else None
    )


def object_name(path: str) -> str:
    name = str(path).rsplit(".", 1)[-1]
    return name[:-2] if name.endswith("_C") else name


def period_label(config_id: str) -> str:
    ordinal = str(config_id).rsplit("_", 1)[-1]
    return f"第 {ordinal} 期" if ordinal.isdigit() else "正式期数"


def home_badge(mode: str, entries: Iterable[CatalogEntry]) -> str:
    """Summarize one home category without duplicating records in the view."""

    if mode == "outer_realm":
        return "当期与预计期"
    labels = {
        "official_illustrated": "名大世界敌人",
        "feast": "名挑战对象",
        "clone": "个副本",
        "world_boss": "名追猎目标",
        "high_risk": "项高危委托",
    }
    return f"{len({entry.primary_id for entry in entries})} {labels[mode]}"
