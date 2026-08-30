# 将怪物和玩法静态事实投影为 Qt 无关的资料库 DTO。
"""Qt-free monster and encounter catalog service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote
from zoneinfo import ZoneInfo

from src.storage.sqlite.static_catalog_monster_queries import (
    StaticCatalogMonsterQueries,
)


OFFICIAL = "official_static"
FORMULA = "formula_profile"
DERIVED = "project_annotation"
UNAVAILABLE = "unavailable"

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
}


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
    schema_version: int = 29
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


def _display(value: object) -> str:
    if value is None or value == "":
        return "不可用"
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


class StaticCatalogMonsterService:
    """Builds display DTOs without changing or inferring static identity."""

    def __init__(
        self,
        queries: StaticCatalogMonsterQueries,
        *,
        mainland_now: datetime | None = None,
    ) -> None:
        self._queries = queries
        now = mainland_now or datetime.now(ZoneInfo("Asia/Shanghai"))
        if now.tzinfo is not None:
            now = now.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
        self._mainland_now = now

    @classmethod
    def from_database(
        cls,
        database_path: str | Path | None = None,
        *,
        mainland_now: datetime | None = None,
    ) -> "StaticCatalogMonsterService":
        return cls(
            StaticCatalogMonsterQueries(database_path),
            mainland_now=mainland_now,
        )

    def close(self) -> None:
        self._queries.close()

    def dataset(self) -> CatalogDataset:
        metadata = self._queries.catalog_metadata()
        return CatalogDataset(
            dataset_id=str(metadata.get("dataset_id") or ""),
            importer_version=int(metadata.get("importer_version") or 0),
            built_at_utc=str(metadata.get("built_at_utc") or ""),
        )

    def list_entries(self, filters: CatalogFilter = CatalogFilter()) -> CatalogPage:
        rows, total = self._queries.list_catalog_index(
            search=filters.search,
            domain=filters.domain,
            play_mode=filters.play_mode,
            region=filters.region,
            difficulty=filters.difficulty,
            version=filters.version,
            release_scope=filters.release_scope,
            as_of_mainland=self._mainland_now.strftime("%Y-%m-%dT%H:%M:%S"),
            limit=filters.page_size,
            offset=filters.offset,
        )
        items = tuple(self._entry_from_row(row) for row in rows)
        return CatalogPage(
            items=items,
            total=total,
            offset=max(0, filters.offset),
            page_size=max(1, min(filters.page_size, 200)),
            has_more=max(0, filters.offset) + len(items) < total,
        )

    def get_detail(self, key: str) -> CatalogDetail | None:
        kind, parts = _parse_key(key)
        if kind in {"manual_monster", "world_boss"} and len(parts) == 1:
            return self._manual_detail(parts[0], world_boss=kind == "world_boss")
        if kind == "profile_monster" and len(parts) == 2:
            return self._profile_detail(parts[0], parts[1])
        if kind == "feast" and len(parts) == 2:
            return self._feast_detail(parts[0], int(parts[1]))
        if kind == "outer_realm" and len(parts) == 3:
            return self._outer_detail(parts[0], int(parts[1]), parts[2])
        if kind == "clone" and len(parts) == 2:
            return self._clone_detail(parts[0], int(parts[1]))
        if kind == "high_risk" and len(parts) == 2:
            return self._high_risk_detail(parts[0], int(parts[1]))
        raise ValueError(f"不支持的怪物/玩法资料键：{key}")

    def _entry_from_row(self, row: dict[str, Any]) -> CatalogEntry:
        fallback = str(row.get("primary_id") or row.get("secondary_id") or "未命名记录")
        title, localization_available = _text_state(row.get("title_zh"), fallback)
        play_mode = str(row.get("play_mode") or "")
        region, region_available = _text_state(
            row.get("region"), _PLAY_MODE_LABELS.get(play_mode, play_mode)
        )
        release_state = str(row.get("release_state") or "")
        subtitle_parts = [_PLAY_MODE_LABELS.get(play_mode, play_mode), region]
        if row.get("difficulty"):
            subtitle_parts.append(f"难度 / 层：{row['difficulty']}")
        if release_state:
            subtitle_parts.append(_RELEASE_LABELS.get(release_state, release_state))
        key_parts = [row.get("identity_1", "")]
        if row.get("identity_2") != "":
            key_parts.append(row["identity_2"])
        if row.get("identity_3") != "":
            key_parts.append(row["identity_3"])
        return CatalogEntry(
            key=_key(str(row["entity_kind"]), *key_parts),
            domain=str(row.get("domain") or ""),
            play_mode=play_mode,
            title=title,
            subtitle=" · ".join(part for part in subtitle_parts if part),
            primary_id=str(row.get("primary_id") or ""),
            secondary_id=str(row.get("secondary_id") or ""),
            resource_path=str(row.get("resource_path") or ""),
            release_state=release_state,
            localization_available=localization_available and region_available,
        )

    @staticmethod
    def _entry(
        key: str,
        *,
        domain: str,
        play_mode: str,
        title_value: object,
        fallback: str,
        subtitle: str,
        primary_id: str,
        secondary_id: str = "",
        resource_path: str = "",
        release_state: str = "",
    ) -> CatalogEntry:
        title, available = _text_state(title_value, fallback)
        return CatalogEntry(
            key=key,
            domain=domain,
            play_mode=play_mode,
            title=title,
            subtitle=subtitle,
            primary_id=primary_id,
            secondary_id=secondary_id,
            resource_path=resource_path,
            release_state=release_state,
            localization_available=available,
        )

    @staticmethod
    def _value(
        label: str,
        value: object,
        provenance: str = OFFICIAL,
        *,
        copyable: bool = False,
        note: str = "",
    ) -> CatalogValue:
        return CatalogValue(label, _display(value), provenance, copyable, note)

    @staticmethod
    def _localized_value(label: str, value: object) -> CatalogValue:
        text, available = _text_state(value, "不可用（静态库中文文本损坏）")
        return CatalogValue(label, text, OFFICIAL if available else UNAVAILABLE)

    def _source_section(self, source: dict[str, Any] | None) -> CatalogSection:
        if not source:
            return CatalogSection(
                "来源追溯",
                (self._value("来源", None, UNAVAILABLE),),
                "当前规范化记录没有可用的 source_row_id。",
            )
        return CatalogSection(
            "来源追溯",
            (
                self._value("来源文件", source.get("relative_path"), copyable=True),
                self._value("源行键", source.get("row_key"), copyable=True),
                self._value("源行 SHA-256", source.get("content_sha256"), copyable=True),
                self._value("源文件 SHA-256", source.get("source_file_sha256"), copyable=True),
                self._value(
                    "原始 payload",
                    "可用" if source.get("payload_available") else "不可用（发行库已省略）",
                    OFFICIAL if source.get("payload_available") else UNAVAILABLE,
                ),
            ),
        )

    def _combat_profile_section(
        self,
        profile: dict[str, Any] | None,
        *,
        title: str = "等价公式画像",
        level: object = None,
        note: str = "公式画像不等于怪物正式身份。",
    ) -> CatalogSection:
        if not profile:
            return CatalogSection(
                title,
                (self._value("画像", None, UNAVAILABLE),),
                note,
            )
        values = [
            self._value("怪物等级", level, FORMULA),
            self._value("profile_set", profile.get("profile_set"), FORMULA, copyable=True),
            self._value("pack_id", profile.get("pack_id"), FORMULA, copyable=True),
            self._value("生命基础", profile.get("health_base"), FORMULA),
            self._value("生命加成", profile.get("health_up"), FORMULA),
            self._value("生命固定值", profile.get("health_add"), FORMULA),
            self._value("防御基础", profile.get("defense_base"), FORMULA),
            self._value("防御加成", profile.get("defense_up"), FORMULA),
            self._value("防御固定值", profile.get("defense_add"), FORMULA),
            self._value("防御忽略", profile.get("defense_ignore"), FORMULA),
            self._value("倾陷上限", profile.get("topple_limit"), FORMULA),
            self._value("倾陷恢复", profile.get("topple_reduce_reset"), FORMULA),
            self._value(
                "攻击档",
                "不可用（schema v29 无攻击属性字段）",
                UNAVAILABLE,
            ),
        ]
        for resistance in profile.get("resistances", ()):
            values.append(self._value(
                f"抗性 {resistance['damage_type']}",
                f"{_display(resistance.get('resistance_base'))} / 免疫 {_display(resistance.get('immunity'))}",
                FORMULA,
            ))
        return CatalogSection(title, tuple(values), note)

    def _manual_detail(self, manual_id: str, *, world_boss: bool) -> CatalogDetail | None:
        row = self._queries.manual_monster(manual_id)
        if row is None:
            return None
        mode = "world_boss" if world_boss else "official_illustrated"
        entry = self._entry(
            _key("world_boss" if world_boss else "manual_monster", manual_id),
            domain="encounter" if world_boss else "monster",
            play_mode=mode,
            title_value=row.get("name_zh"),
            fallback=manual_id,
            subtitle=_PLAY_MODE_LABELS[mode],
            primary_id=manual_id,
            secondary_id=str(row.get("enemy_type") or ""),
            resource_path=str(row.get("world_image_path") or row.get("image_path") or ""),
        )
        values = (
            self._value("正式图鉴 ID", manual_id, copyable=True),
            self._localized_value("中文名", row.get("name_zh")),
            self._value("敌人类型", row.get("enemy_type")),
            self._localized_value("地区 / 位置", row.get("place_zh")),
            self._value("追踪类型", row.get("trace_type")),
            self._value("掉落 ID", row.get("drop_id"), copyable=True),
            self._value("图标路径", row.get("image_path"), copyable=True),
            self._value("大世界图路径", row.get("world_image_path"), copyable=True),
        )
        sections = [CatalogSection("正式身份", values)]
        if row.get("aliases"):
            sections.append(CatalogSection("正式别名", tuple(
                self._value(
                    str(alias.get("alias_kind") or "alias"),
                    alias.get("alias_value"),
                    copyable=True,
                )
                for alias in row["aliases"]
            )))
        relations: list[CatalogRelation] = []
        bindings = [
            binding for binding in row.get("bindings", ())
            if not world_boss or binding.get("binding_kind") == "world_boss_id"
        ]
        for binding in bindings:
            sections.append(CatalogSection(
                f"模板绑定 · {binding['binding_kind']}",
                (
                    self._value("模板 ID", binding.get("monster_template_name"), copyable=True),
                    self._value("绑定类型", binding.get("binding_kind")),
                ),
                "这是静态库显式绑定，不是按中文名匹配。",
            ))
            for profile in binding.get("profiles", ()):
                relations.append(CatalogRelation(
                    f"查看模板画像：{profile['static_table']}",
                    _key("profile_monster", profile["static_table"], profile["monster_id"]),
                    "explicit_template_binding",
                    "由 monster_template_binding 的正式模板名连接。",
                ))
        if not world_boss and row.get("enemy_type") == "WeeklyBoss":
            relations.append(CatalogRelation(
                "查看异象追猎玩法条目",
                _key("world_boss", manual_id),
                "official_enemy_type",
            ))
        sections.append(self._source_section(row.get("source")))
        notices = () if entry.localization_available else (
            "该记录的中文文本在发行静态库中已含替换字符，本页不修复或补猜。",
        )
        return CatalogDetail(entry, tuple(sections), unique_relations(relations), notices)

    def _profile_detail(self, static_table: str, monster_id: str) -> CatalogDetail | None:
        row = self._queries.profile_monster(static_table, monster_id)
        if row is None:
            return None
        bindings = row.get("manual_bindings", ())
        title_value = bindings[0].get("name_zh") if bindings else None
        entry = self._entry(
            _key("profile_monster", static_table, monster_id),
            domain="monster",
            play_mode="template_profile",
            title_value=title_value,
            fallback=monster_id,
            subtitle=f"{_PLAY_MODE_LABELS['template_profile']} · {static_table}",
            primary_id=monster_id,
            secondary_id=static_table,
        )
        sections = [CatalogSection("正式模板记录", (
            self._value("静态表", static_table, copyable=True),
            self._value("怪物模板 ID", monster_id, copyable=True),
            self._value("默认等级", row.get("monster_level")),
            self._value("online_ratio_id", row.get("online_ratio_id"), copyable=True),
        ))]
        default_profile = None
        if row.get("default_profile_set") and row.get("default_pack_id"):
            default_profile = self._queries.combat_profile(
                row["default_profile_set"], row["default_pack_id"]
            )
        sections.append(self._combat_profile_section(
            default_profile,
            level=row.get("monster_level"),
            title="默认等价公式画像",
        ))
        for variant in row.get("variants", ()):
            sections.append(self._combat_profile_section(
                self._queries.combat_profile(variant["profile_set"], variant["pack_id"]),
                level=variant.get("threshold_level"),
                title=f"等级画像 · {variant['variant_kind']} · {variant['threshold_level']}",
            ))
        relations: list[CatalogRelation] = [CatalogRelation(
            f"正式图鉴：{binding['monster_manual_id']}",
            _key("manual_monster", binding["monster_manual_id"]),
            "explicit_template_binding",
            f"绑定类型：{binding['binding_kind']}",
        ) for binding in bindings]
        for reference in self._queries.template_encounter_references(monster_id):
            kind = str(reference["entity_kind"])
            parts = [reference["identity_1"]]
            if reference.get("identity_2") != "":
                parts.append(reference["identity_2"])
            if reference.get("identity_3") != "":
                parts.append(reference["identity_3"])
            title, _available = _text_state(reference.get("title_zh"), kind)
            relations.append(CatalogRelation(
                f"玩法引用：{_PLAY_MODE_LABELS.get(kind, kind)} · {title}",
                _key(kind, *parts),
                str(reference.get("relation_kind") or "exact_official_reference"),
                "来自正式模板 ID 或 Unreal 类路径对象名，不使用中文名。",
            ))
        sections.append(self._source_section(row.get("source")))
        notice = (
            "怪物模板 ID 是正式静态字段；只有显式 monster_template_binding "
            "才建立与图鉴 ID 的身份关系。共用数值画像不证明身份。"
        )
        return CatalogDetail(entry, tuple(sections), unique_relations(relations), (notice,))

    def _feast_detail(self, stage_id: str, difficulty_id: int) -> CatalogDetail | None:
        row = self._queries.feast_encounter(stage_id, difficulty_id)
        if row is None:
            return None
        entry = self._entry(
            _key("feast", stage_id, difficulty_id), domain="encounter", play_mode="feast",
            title_value=row.get("name_zh"), fallback=stage_id,
            subtitle=f"争锋赏宴 · 难度 {difficulty_id}", primary_id=stage_id,
            secondary_id=str(row.get("boss_monster_id") or ""),
            resource_path=str(row.get("boss_icon_path") or ""),
        )
        sections = [CatalogSection("正式玩法配置", (
            self._value("挑战对象 ID", stage_id, copyable=True),
            self._localized_value("挑战名", row.get("name_zh")),
            self._value("Boss 模板 ID", row.get("boss_monster_id"), copyable=True),
            self._localized_value("Boss 中文名", row.get("boss_name_zh")),
            self._value("难度", difficulty_id),
            self._value("怪物等级", row.get("monster_level")),
            self._value("基础分", row.get("base_score")),
            self._value("得分倍率", row.get("score_rate")),
            self._value("特殊高难", bool(row.get("special_high_difficulty"))),
        ))]
        sections.append(self._combat_profile_section(
            row.get("profile"), level=row.get("monster_level"), title="本难度公式画像"
        ))
        if row.get("options"):
            option_values: list[CatalogValue] = []
            for option in row["options"]:
                category, _available = _text_state(
                    option.get("category_name_zh"), str(option["category_ordinal"])
                )
                option_values.append(self._value(
                    f"{category} · {option['option_id']}",
                    f"类型 {option.get('option_type')} / 效果 {option.get('effect_kind')} / "
                    f"伤害类型 {option.get('damage_type') or '-'} / "
                    f"数值 {_display(option.get('add_value'))} / "
                    f"限时 {_display(option.get('limit_seconds'))} / "
                    f"得分 {_display(option.get('score'))}",
                ))
                if option.get("buff_asset_path"):
                    option_values.append(self._value(
                        f"{option['option_id']} Buff 路径",
                        option["buff_asset_path"],
                        copyable=True,
                    ))
            sections.append(CatalogSection("官方加成选项", tuple(option_values)))
        relations = []
        for profile in self._queries.template_profile_candidates(row["boss_monster_id"]):
            relations.append(CatalogRelation(
                f"查看 Boss 模板：{profile['static_table']}",
                _key("profile_monster", profile["static_table"], profile["monster_id"]),
                "exact_official_template_id",
            ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))

    def _release_state(self, starts: object, ends: object) -> str:
        start = datetime.fromisoformat(str(starts))
        end = datetime.fromisoformat(str(ends))
        if start <= self._mainland_now <= end:
            return "current"
        if end < self._mainland_now:
            return "historical"
        future_start = self._queries.next_outer_realm_start(
            self._mainland_now.strftime("%Y-%m-%dT%H:%M:%S")
        )
        return "next" if future_start == str(starts) else "scheduled"

    def _outer_detail(
        self, config_id: str, level_id: int, fight_stage: str,
    ) -> CatalogDetail | None:
        row = self._queries.outer_realm_encounter(config_id, level_id, fight_stage)
        if row is None:
            return None
        state = self._release_state(row["starts_at_mainland"], row["ends_at_mainland"])
        title, _ = _text_state(row.get("name_zh"), f"{config_id} · 第 {level_id} 层")
        entry = self._entry(
            _key("outer_realm", config_id, level_id, fight_stage),
            domain="encounter", play_mode="outer_realm", title_value=title,
            fallback=f"{config_id} · 第 {level_id} 层",
            subtitle=f"轨外之境 · {_RELEASE_LABELS[state]} · {fight_stage}",
            primary_id=config_id, secondary_id=fight_stage, release_state=state,
        )
        sections = [CatalogSection("正式期数与分区", (
            self._value("配置 ID", config_id, copyable=True),
            self._value("层", level_id),
            self._value("上/下半场", fight_stage, copyable=True),
            self._value("大陆服开始", row.get("starts_at_mainland")),
            self._value("大陆服结束", row.get("ends_at_mainland")),
            self._value("当前/下一期状态", _RELEASE_LABELS[state], DERIVED),
        ), "期数状态由大陆服开始/结束时间与查询时刻比较得出。")]
        relations: list[CatalogRelation] = []
        for index, member in enumerate(row.get("members", ()), 1):
            sections.append(CatalogSection(f"刷怪槽位 {index}", (
                self._value("刷怪池 ID", member.get("monster_pool_id"), copyable=True),
                self._value("刷怪顺序", member.get("spawn_ordinal")),
                self._value("波次", member.get("wave")),
                self._value("下一刷新方式", member.get("next_spawn_type")),
                self._value("刷新时间", member.get("spawn_time")),
                self._value("怪物顺序", member.get("monster_ordinal")),
                self._value("怪物类路径", member.get("monster_class_path"), copyable=True),
                self._localized_value("怪物中文名", member.get("monster_name_zh")),
                self._value("数量", member.get("monster_count")),
                self._value("等级", member.get("monster_level")),
            )))
            sections.append(self._combat_profile_section(
                member.get("profile"), level=member.get("monster_level"),
                title=f"刷怪槽位 {index} 公式画像",
            ))
            relations.extend(self._path_relations(member.get("monster_class_path")))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))

    def _clone_detail(self, clone_id: str, ordinal: int) -> CatalogDetail | None:
        row = self._queries.clone_encounter(clone_id, ordinal)
        if row is None:
            return None
        entry = self._entry(
            _key("clone", clone_id, ordinal), domain="encounter", play_mode="clone",
            title_value=row.get("name_zh"), fallback=clone_id,
            subtitle=f"{_PLAY_MODE_LABELS['clone']} · 难度 {ordinal}",
            primary_id=clone_id, secondary_id=str(row.get("clone_type") or ""),
        )
        sections = [CatalogSection("正式副本配置", (
            self._value("副本 ID", clone_id, copyable=True),
            self._value(
                "副本类型", row.get("clone_type"),
                OFFICIAL if row.get("clone_type") else UNAVAILABLE,
                copyable=bool(row.get("clone_type")),
            ),
            self._localized_value("类目", row.get("category_name_zh")),
            self._value(
                "冒险手册展示",
                bool(row.get("show_in_adventure")) if row.get("show_in_adventure") is not None else None,
                OFFICIAL if row.get("show_in_adventure") is not None else UNAVAILABLE,
            ),
            self._value(
                "跨场景",
                bool(row.get("cross_scene")) if row.get("cross_scene") is not None else None,
                OFFICIAL if row.get("cross_scene") is not None else UNAVAILABLE,
            ),
            self._value("难度序号", ordinal),
            self._value("难度等级", row.get("difficulty_level")),
            self._value("队伍等级", row.get("team_level")),
            self._value("体力", row.get("stamina_cost")),
            self._value("掉落 ID", row.get("drop_id"), copyable=True),
            self._value("刷怪配置 ID", row.get("spawn_id"), copyable=True),
            self._value("击杀时限", row.get("kill_monster_time_limit")),
        ))]
        relations: list[CatalogRelation] = []
        members = row.get("members", ())
        if not members:
            sections.append(CatalogSection("刷怪槽位", (
                self._value("正式怪物池", None, UNAVAILABLE),
            )))
        for index, member in enumerate(members, 1):
            sections.append(CatalogSection(f"刷怪槽位 {index}", (
                self._value("波次", member.get("wave_ordinal")),
                self._value("模板路径", member.get("monster_template_path"), copyable=True),
                self._value("模板 ID", member.get("monster_template_name"), copyable=True),
                self._value("数量", member.get("monster_count")),
            )))
            profiles = self._queries.template_profile_candidates(
                member.get("monster_template_name") or ""
            )
            for candidate in profiles:
                relations.append(CatalogRelation(
                    f"刷怪槽位 {index} 模板画像：{candidate['static_table']}",
                    _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                    "exact_official_template_id",
                ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))

    def _high_risk_detail(self, commission_id: str, difficulty: int) -> CatalogDetail | None:
        row = self._queries.high_risk_encounter(commission_id, difficulty)
        if row is None:
            return None
        entry = self._entry(
            _key("high_risk", commission_id, difficulty), domain="encounter",
            play_mode="high_risk", title_value=row.get("name_zh"), fallback=commission_id,
            subtitle=f"高危委托 · 难度 {difficulty}", primary_id=commission_id,
            secondary_id=str(row.get("monster_pool_id") or ""),
        )
        sections = [CatalogSection("正式委托配置", (
            self._value("委托 ID", commission_id, copyable=True),
            self._localized_value("委托名", row.get("name_zh")),
            self._value("难度", difficulty),
            self._value("推荐角色等级", row.get("recommended_character_level")),
            self._value("场景 ID", row.get("scene_data_id"), copyable=True),
            self._value("逐难度怪物池", row.get("monster_pool_id"), copyable=True),
            self._value("通用回退池", row.get("fallback_monster_pool_id"), copyable=True),
        ))]
        relations: list[CatalogRelation] = []
        members = row.get("members", ())
        if not row.get("monster_pool_id"):
            sections.append(CatalogSection("逐难度正式怪物池", (
                self._value("怪物池", "不可用（本难度只有通用回退池）", UNAVAILABLE),
            )))
        for index, member in enumerate(members, 1):
            sections.append(CatalogSection(f"怪物池成员 {index}", (
                self._value("类路径", member.get("monster_class_path"), copyable=True),
                self._value("模板 ID", member.get("monster_template_name"), copyable=True),
                self._value("数量", member.get("monster_count")),
                self._value("配置等级", member.get("configured_monster_level")),
                self._value("属性 ID", member.get("attribute_id"), copyable=True),
            )))
            for candidate in self._queries.template_profile_candidates(
                member.get("monster_template_name") or ""
            ):
                relations.append(CatalogRelation(
                    f"怪物池成员 {index} 模板画像：{candidate['static_table']}",
                    _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                    "exact_official_template_id",
                ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))

    def _path_relations(self, class_path: object) -> list[CatalogRelation]:
        path = str(class_path or "").strip()
        if not path or "." not in path:
            return []
        object_name = path.rsplit(".", 1)[-1]
        if object_name.endswith("_C"):
            object_name = object_name[:-2]
        relations = []
        for candidate in self._queries.template_profile_candidates(object_name):
            relations.append(CatalogRelation(
                f"类路径对象的模板画像：{candidate['static_table']}",
                _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                "exact_class_path_object",
                "仅按正式类路径中的对象名精确连接，不使用中文名。",
            ))
        return relations


def provenance_label(provenance: str) -> str:
    return {
        OFFICIAL: "官方静态事实",
        FORMULA: "等价公式画像",
        DERIVED: "项目派生 / 注解",
        UNAVAILABLE: "不可用",
    }.get(provenance, provenance)


def play_mode_choices() -> tuple[tuple[str, str], ...]:
    return (("all", "全部玩法"), *tuple(_PLAY_MODE_LABELS.items()))


def release_scope_choices() -> tuple[tuple[str, str], ...]:
    return (
        ("all", "全部期数"),
        ("current_next", "当前 + 下一期"),
        ("current", "仅当前期"),
        ("next", "仅下一期"),
    )


def unique_relations(relations: Iterable[CatalogRelation]) -> tuple[CatalogRelation, ...]:
    unique: dict[tuple[str, str], CatalogRelation] = {}
    for relation in relations:
        unique.setdefault((relation.target_key, relation.relation_kind), relation)
    return tuple(unique.values())
