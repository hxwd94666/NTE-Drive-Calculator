# 将怪物和玩法静态事实投影为 Qt 无关的资料库 DTO。
"""Qt-free monster and encounter catalog service."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote, unquote

from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_catalog_scalar,
    display_damage_type,
    display_fight_stage,
)
from src.services.static_catalog_feast_period_service import (
    StaticCatalogFeastPeriodMixin,
)
from src.services.static_catalog_monster_gameplay import (
    StaticCatalogMonsterGameplayProjector,
)
from src.services.static_catalog_monster_models import (
    CatalogDataset,
    CatalogDetail,
    CatalogEntry,
    CatalogFilter,
    CatalogPage,
    CatalogRelation,
    CatalogSection,
    CatalogValue,
)
from src.services.static_catalog_outer_realm_detail import (
    StaticCatalogOuterRealmDetailMixin,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_monster_queries import (
    StaticCatalogMonsterQueries,
)


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


class StaticCatalogMonsterService(
    StaticCatalogFeastPeriodMixin,
    StaticCatalogOuterRealmDetailMixin,
):
    """Builds display DTOs without changing or inferring static identity."""

    def __init__(
        self,
        queries: StaticCatalogMonsterQueries,
        *,
        terminology_service: StaticCatalogTerminologyService,
        mainland_now: datetime | None = None,
    ) -> None:
        self._queries = queries
        self._terminology_service = terminology_service
        self._gameplay = StaticCatalogMonsterGameplayProjector(terminology_service)
        now = mainland_now or datetime.now(_MAINLAND_TIMEZONE)
        if now.tzinfo is not None:
            now = now.astimezone(_MAINLAND_TIMEZONE).replace(tzinfo=None)
        self._mainland_now = now

    @classmethod
    def from_database(
        cls,
        database_path: str | Path | None = None,
        *,
        terminology_service: StaticCatalogTerminologyService,
        mainland_now: datetime | None = None,
    ) -> "StaticCatalogMonsterService":
        return cls(
            StaticCatalogMonsterQueries(database_path),
            terminology_service=terminology_service,
            mainland_now=mainland_now,
        )

    @property
    def terminology_service(self) -> StaticCatalogTerminologyService:
        """Return the frozen central terminology dependency for composition checks."""

        return self._terminology_service

    def close(self) -> None:
        self._queries.close()

    def dataset(self) -> CatalogDataset:
        metadata = self._queries.catalog_metadata()
        return CatalogDataset(
            dataset_id=str(metadata.get("dataset_id") or ""),
            importer_version=int(metadata.get("importer_version") or 0),
            built_at_utc=str(metadata.get("built_at_utc") or ""),
        )

    def list_witch_blessings(self) -> tuple[CatalogEntry, ...]:
        """Return the seven formal battle-wide blessing choices."""

        return self._gameplay.witch_entries(self._queries.list_divination_buffs())

    def profile_family_keys(self, monster_id: str) -> tuple[str, ...]:
        """Return profile keys from the same explicit formal ID family."""

        return tuple(
            _key("profile_monster", row["static_table"], row["monster_id"])
            for row in self._queries.profile_family_candidates(monster_id)
        )

    def clone_drop_status_counts(self) -> dict[str, int]:
        """Return gameplay-difficulty coverage for the v30 drop closure."""

        return self._queries.clone_drop_status_counts()

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
        if kind == "feast_period" and len(parts) == 3:
            return self.get_feast_detail(parts[0], parts[1], int(parts[2]))
        if kind == "outer_realm" and len(parts) == 3:
            return self._outer_detail(parts[0], int(parts[1]), parts[2])
        if kind == "outer_member" and len(parts) == 6:
            return self._outer_member_detail(
                parts[0], int(parts[1]), parts[2], int(parts[3]),
                parts[4], int(parts[5]),
            )
        if kind == "clone" and len(parts) == 2:
            return self._clone_detail(parts[0], int(parts[1]))
        if kind == "high_risk" and len(parts) == 2:
            return self._high_risk_detail(parts[0], int(parts[1]))
        if kind == "witch_buff" and len(parts) == 1:
            row = self._queries.divination_buff(parts[0])
            return self._gameplay.witch_detail(row) if row is not None else None
        if kind == "outer_buff" and len(parts) == 1:
            row = self._queries.outer_realm_season_buff(parts[0])
            return self._gameplay.outer_buff_detail(row) if row is not None else None
        raise ValueError(f"不支持的怪物/玩法资料键：{key}")

    def _entry_from_row(self, row: dict[str, Any]) -> CatalogEntry:
        title, localization_available = _text_state(row.get("title_zh"), NAME_UNAVAILABLE)
        play_mode = str(row.get("play_mode") or "")
        region, region_available = _text_state(
            row.get("region"), _PLAY_MODE_LABELS.get(play_mode, play_mode)
        )
        release_state = str(row.get("release_state") or "")
        subtitle_parts = list(dict.fromkeys((
            _PLAY_MODE_LABELS.get(play_mode, play_mode), region,
        )))
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
            secondary_label=(
                display_fight_stage(
                    self._terminology_service, row.get("secondary_id"),
                )
                if play_mode == "outer_realm" else ""
            ),
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
        secondary_label: str = "",
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
            secondary_label=secondary_label,
        )

    @staticmethod
    def _value(
        label: str,
        value: object,
        provenance: str = OFFICIAL,
        *,
        copyable: bool = False,
        note: str = "",
        display_label: str = "",
        display_value: str = "",
    ) -> CatalogValue:
        return CatalogValue(
            label=label,
            value=display_catalog_scalar(value),
            provenance=provenance,
            copyable=copyable,
            note=note,
            display_label=display_label,
            display_value=display_value,
        )

    @staticmethod
    def _localized_value(label: str, value: object) -> CatalogValue:
        text, available = _text_state(value, NAME_UNAVAILABLE)
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
        variant_kind: str = "",
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
                "不可用（schema v30 无攻击属性字段）",
                UNAVAILABLE,
                display_value="暂无数据",
            ),
        ]
        if variant_kind:
            values.append(self._value("画像档位类型", variant_kind, FORMULA))
        for resistance in profile.get("resistances", ()):
            damage_type = str(resistance["damage_type"])
            values.append(self._value(
                f"抗性 {damage_type}",
                f"{display_catalog_scalar(resistance.get('resistance_base'))} / "
                f"免疫 {display_catalog_scalar(resistance.get('immunity'))}",
                FORMULA,
                display_label=display_damage_type(
                    self._terminology_service, damage_type,
                ),
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
            fallback=NAME_UNAVAILABLE,
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
        family_evidence_row = None
        if not bindings:
            family_evidence = self._queries.profile_family_display_evidence(
                monster_id
            )
            names = {
                str(candidate.get("name_zh") or "").strip()
                for candidate in family_evidence
                if _text_state(candidate.get("name_zh"), "")[1]
            }
            if len(names) == 1:
                family_evidence_row = next(
                    candidate for candidate in family_evidence
                    if str(candidate.get("name_zh") or "").strip() in names
                )
        title_value = (
            bindings[0].get("name_zh")
            if bindings else (
                family_evidence_row.get("name_zh")
                if family_evidence_row else None
            )
        )
        entry = self._entry(
            _key("profile_monster", static_table, monster_id),
            domain="monster",
            play_mode="template_profile",
            title_value=title_value,
            fallback=NAME_UNAVAILABLE,
            subtitle=_PLAY_MODE_LABELS["template_profile"],
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
                variant_kind=str(variant.get("variant_kind") or ""),
                title=f"等级画像 · 等级 {variant['threshold_level']}",
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
            "标题使用同一正式 mon/boss 数字家族中全部可用名称证据的唯一结果；"
            "这只补充展示，不建立当前模板与图鉴的身份关系。"
            if family_evidence_row else (
                "怪物模板 ID 是正式静态字段；只有显式 monster_template_binding "
                "才建立与图鉴 ID 的身份关系。共用数值画像不证明身份。"
            )
        )
        return CatalogDetail(entry, tuple(sections), unique_relations(relations), (notice,))

    def _release_state(self, starts: object, ends: object) -> str:
        if not starts or not ends:
            return "unscheduled"
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

    def _clone_detail(self, clone_id: str, ordinal: int) -> CatalogDetail | None:
        row = self._queries.clone_encounter(clone_id, ordinal)
        if row is None:
            return None
        entry = self._entry(
            _key("clone", clone_id, ordinal), domain="encounter", play_mode="clone",
            title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
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
            self._value("刷怪配置 ID", row.get("spawn_id"), copyable=True),
            self._value("击杀时限", row.get("kill_monster_time_limit")),
        ))]
        sections.append(self._gameplay.drop_section(row.get("drop_projection")))
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
            play_mode="high_risk", title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
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
