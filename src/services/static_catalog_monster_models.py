# 怪物与玩法资料库的 Qt 无关公开 DTO。
"""Immutable public records returned by the monster catalog service."""

from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class CatalogFilter:
    search: str = ""
    domain: str = "all"
    play_mode: str = "all"
    region: str = ""
    difficulty: str = ""
    version: str = ""
    release_scope: str = "all"
    page_size: int = 50
    offset: int = 0


@dataclass(frozen=True)
class CatalogDataset:
    dataset_id: str
    importer_version: int
    built_at_utc: str
    schema_version: int = 31
    read_only: bool = True


@dataclass(frozen=True)
class CatalogEntry:
    key: str
    domain: str
    play_mode: str
    title: str
    subtitle: str
    primary_id: str
    secondary_id: str = ""
    resource_path: str = ""
    release_state: str = ""
    localization_available: bool = True
    secondary_label: str = ""


@dataclass(frozen=True)
class CatalogPage:
    items: tuple[CatalogEntry, ...]
    total: int
    offset: int
    page_size: int
    has_more: bool


@dataclass(frozen=True)
class CatalogValue:
    label: str
    value: str
    provenance: str
    copyable: bool = False
    note: str = ""
    display_label: str = ""
    display_value: str = ""


@dataclass(frozen=True)
class CatalogSection:
    title: str
    values: tuple[CatalogValue, ...]
    note: str = ""


@dataclass(frozen=True)
class CatalogRelation:
    label: str
    target_key: str
    relation_kind: str
    note: str = ""


@dataclass(frozen=True)
class CatalogDetail:
    entry: CatalogEntry
    sections: tuple[CatalogSection, ...]
    relations: tuple[CatalogRelation, ...] = ()
    notices: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeastDifficultyChoice:
    difficulty_id: int
    display_name: str
    monster_level: int
    score_rate: float


@dataclass(frozen=True)
class FeastOptionChoice:
    option_id: str
    display_name: str


@dataclass(frozen=True)
class FeastOptionGroup:
    group_id: str
    display_name: str
    options: tuple[FeastOptionChoice, ...]


@dataclass(frozen=True)
class FeastSetup:
    period_id: str
    period_label: str
    period_state: str
    schedule_label: str
    stage_id: str
    title: str
    boss_name: str
    boss_monster_id: str
    challenge_ordinal: int
    default_difficulty_id: int
    difficulties: tuple[FeastDifficultyChoice, ...]
    option_groups: tuple[FeastOptionGroup, ...]
    condition_note: str = ""


@dataclass(frozen=True)
class FeastPeriod:
    period_id: str
    display_label: str
    release_state: str
    schedule_label: str
    challenge_ids: tuple[str, ...]
    evidence_label: str
    evidence_url: str
