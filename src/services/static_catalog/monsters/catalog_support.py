# 怪物资料共用的资料键、取词与来源常量；service 与各详情 mixin 共用，不复制实现。
from __future__ import annotations

from datetime import timedelta, timezone
from typing import Iterable
from urllib.parse import quote, unquote

from src.domain.localization.catalog import TextCatalog, localized_text
from src.services.static_catalog.monsters.models import CatalogRelation


OFFICIAL = "official_static"

FORMULA = "formula_profile"

DERIVED = "project_annotation"

UNAVAILABLE = "unavailable"

_MAINLAND_TIMEZONE = timezone(timedelta(hours=8))

_PLAY_MODE_LABELS = {
    "official_illustrated": "官方图鉴 / 大世界",
    "template_profile": "怪物模板与等级画像",
    "world_boss": "异象追猎单 Boss",
    "feast": "争锋赏宴",
    "outer_realm": "轨外之境",
    "clone": "材料 / 养成副本",
    "high_risk": "高危委托",
}

_RELEASE_LABELS = {
    "current": "当前期",
    "next": "下一期",
    "historical": "已结束",
    "scheduled": "待开放",
    "unscheduled": "未提供排期",
}

# 怪物资料文案统一登记在 static_catalog.monster_service 前缀下；无目录时回退下列中文基线。
_PREFIX = "static_catalog.monster_service."

def _text(text_catalog: TextCatalog | None, suffix: str, fallback: str, **values: object) -> str:
    """按怪物资料前缀取词；目录未注入或未登记该 key 时逐字回退原中文。"""

    return localized_text(text_catalog, f"{_PREFIX}{suffix}", fallback, **values)

def _key(kind: str, *parts: object) -> str:
    encoded = [quote(str(part), safe="") for part in parts]
    return "|".join((kind, *encoded))

def _parse_key(key: str) -> tuple[str, tuple[str, ...]]:
    parts = str(key).split("|")
    if not parts or not parts[0]:
        raise ValueError("资料键不能为空")
    return parts[0], tuple(unquote(part) for part in parts[1:])

def _text_state(value: object, fallback: str) -> tuple[str, bool]:
    text = str(value or "").strip()
    if not text or "\ufffd" in text:
        return fallback, False
    return text, True

def unique_relations(relations: Iterable[CatalogRelation]) -> tuple[CatalogRelation, ...]:
    unique: dict[tuple[str, str], CatalogRelation] = {}
    for relation in relations:
        unique.setdefault((relation.target_key, relation.relation_kind), relation)
    return tuple(unique.values())
