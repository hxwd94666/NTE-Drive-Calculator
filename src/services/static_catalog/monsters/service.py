# 将怪物和玩法静态事实投影为 Qt 无关的资料库 DTO。
"""将怪物和玩法静态事实投影为 Qt 无关的资料库 DTO。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.domain.localization.catalog import TextCatalog
from src.integrations.preferences.locale_files import enum_text
from src.services.static_catalog.monsters.display import (
    NAME_UNAVAILABLE,
    display_catalog_scalar,
    display_damage_type,
    display_fight_stage,
)
from src.services.static_catalog.monsters.feast_periods import (
    StaticCatalogFeastPeriodMixin,
)
from src.services.static_catalog.monsters.gameplay import (
    StaticCatalogMonsterGameplayProjector,
)
from src.services.static_catalog.monsters.models import (
    CatalogDataset,
    CatalogDetail,
    CatalogEntry,
    CatalogFilter,
    CatalogPage,
    CatalogRelation,
    CatalogSection,
    CatalogValue,
)
from src.services.static_catalog.monsters.outer_realm import (
    StaticCatalogOuterRealmDetailMixin,
)
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.catalog.static_catalog_monster_queries import (
    StaticCatalogMonsterQueries,
)
from src.services.static_catalog.monsters.activity_details import (
    StaticCatalogActivityDetailMixin,
)
from src.services.static_catalog.monsters.catalog_support import (
    DERIVED,
    FORMULA,
    OFFICIAL,
    UNAVAILABLE,
    unique_relations,
    _MAINLAND_TIMEZONE,
    _PLAY_MODE_LABELS,
    _RELEASE_LABELS,
    _key,
    _parse_key,
    _text,
    _text_state,
)


class StaticCatalogMonsterService(
    StaticCatalogFeastPeriodMixin,
    StaticCatalogOuterRealmDetailMixin,
    StaticCatalogActivityDetailMixin,
):
    """构建怪物与遭遇展示 DTO，不改变或推测静态身份。"""

    def __init__(
        self,
        queries: StaticCatalogMonsterQueries,
        *,
        terminology_service: StaticCatalogTerminologyService,
        mainland_now: datetime | None = None,
        text_catalog: TextCatalog | None = None,
    ) -> None:
        self._queries = queries
        self._terminology_service = terminology_service
        # 目录由组合根注入并冻结；未注入时保持发行中文基线，不另读语言偏好。
        self._text_catalog = text_catalog
        self._gameplay = StaticCatalogMonsterGameplayProjector(
            terminology_service, text_catalog=text_catalog,
        )
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
        text_catalog: TextCatalog | None = None,
    ) -> "StaticCatalogMonsterService":
        return cls(
            StaticCatalogMonsterQueries(database_path),
            terminology_service=terminology_service,
            mainland_now=mainland_now,
            text_catalog=text_catalog,
        )

    @property
    def terminology_service(self) -> StaticCatalogTerminologyService:
        """返回已冻结的公共术语依赖，供组合根核对来源一致性。"""

        return self._terminology_service

    def close(self) -> None:
        self._queries.close()

    def dataset(self) -> CatalogDataset:
        metadata = self._queries.catalog_metadata()
        return CatalogDataset(
            dataset_id=str(metadata.get("dataset_id") or ""),
            importer_version=int(metadata.get("importer_version") or 0),
            built_at_utc=str(metadata.get("built_at_utc") or ""),
            schema_version=int(metadata.get("schema_version") or 0),
        )

    def list_witch_blessings(self) -> tuple[CatalogEntry, ...]:
        """返回正式战斗级赐福选项；不将局部挑战条件混入。"""

        return self._gameplay.witch_entries(self._queries.list_divination_buffs())

    def profile_family_keys(self, monster_id: str) -> tuple[str, ...]:
        """只返回同一明确正式 ID 族中的画像，不按显示名合并。"""

        return tuple(
            _key("profile_monster", row["static_table"], row["monster_id"])
            for row in self._queries.profile_family_candidates(monster_id)
        )

    def clone_drop_status_counts(self) -> dict[str, int]:
        """返回玩法与难度的正式掉落覆盖，不补造缺失难度。"""

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
        if kind == "feast" and len(parts) in {2, 3}:
            return self._feast_detail(parts[0], int(parts[1]), boss_monster_id=parts[2] if len(parts) == 3 else None)
        if kind == "feast_period" and len(parts) in {3, 4}:
            return self.get_feast_detail(parts[0], parts[1], int(parts[2]), boss_monster_id=parts[3] if len(parts) == 4 else None)
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
        raise ValueError(f"不支持的怪物/玩法资料键：{key}")  # ui-text: internal

    def _entry_from_row(self, row: dict[str, Any]) -> CatalogEntry:
        title, localization_available = _text_state(row.get("title_zh"), NAME_UNAVAILABLE)
        catalog = self._text_catalog
        play_mode = str(row.get("play_mode") or "")
        play_labels = enum_text(catalog, _PLAY_MODE_LABELS)
        region, region_available = _text_state(
            row.get("region"), play_labels.get(play_mode, play_mode)
        )
        release_state = str(row.get("release_state") or "")
        subtitle_parts = list(dict.fromkeys((
            play_labels.get(play_mode, play_mode), region,
        )))
        if row.get("difficulty"):
            subtitle_parts.append(_text(
                catalog, "entry.difficulty_floor", "难度 / 层：{value}",
                value=row["difficulty"],
            ))
        if release_state:
            subtitle_parts.append(enum_text(
                catalog, _RELEASE_LABELS.get(release_state, release_state),
            ))
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
        catalog = self._text_catalog
        if not source:
            return CatalogSection(
                _text(catalog, "section.source_trace", "来源追溯"),
                (self._value(_text(catalog, "label.source", "来源"), None, UNAVAILABLE),),
                _text(
                    catalog, "note.source_row_missing",
                    "当前规范化记录没有可用的 source_row_id。",
                ),
            )
        return CatalogSection(
            _text(catalog, "section.source_trace", "来源追溯"),
            (
                self._value(
                    _text(catalog, "label.source_file", "来源文件"),
                    source.get("relative_path"), copyable=True,
                ),
                self._value(
                    _text(catalog, "label.source_row_key", "源行键"),
                    source.get("row_key"), copyable=True,
                ),
                self._value(
                    _text(catalog, "label.source_row_sha256", "源行 SHA-256"),
                    source.get("content_sha256"), copyable=True,
                ),
                self._value(
                    _text(catalog, "label.source_file_sha256", "源文件 SHA-256"),
                    source.get("source_file_sha256"), copyable=True,
                ),
                self._value(
                    _text(catalog, "label.raw_payload", "原始 payload"),
                    _text(catalog, "value.available", "可用")
                    if source.get("payload_available")
                    else _text(catalog, "value.payload_omitted", "不可用（发行库已省略）"),
                    OFFICIAL if source.get("payload_available") else UNAVAILABLE,
                ),
            ),
        )

    def _combat_profile_section(
        self,
        profile: dict[str, Any] | None,
        *,
        title: str | None = None,
        level: object = None,
        variant_kind: str = "",
        note: str = "",
    ) -> CatalogSection:
        catalog = self._text_catalog
        title = title if title is not None else _text(
            catalog, "section.combat_profile", "等价公式画像",
        )
        note = note if note else _text(
            catalog, "note.combat_profile_identity", "公式画像不等于怪物正式身份。",
        )
        if not profile:
            return CatalogSection(
                title,
                (self._value(_text(catalog, "label.profile", "画像"), None, UNAVAILABLE),),
                note,
            )
        values = [
            self._value(_text(catalog, "label.monster_level", "怪物等级"), level, FORMULA),
            self._value("profile_set", profile.get("profile_set"), FORMULA, copyable=True),
            self._value("pack_id", profile.get("pack_id"), FORMULA, copyable=True),
            self._value(_text(catalog, "label.health_base", "生命基础"),
                        profile.get("health_base"), FORMULA),
            self._value(_text(catalog, "label.health_up", "生命加成"),
                        profile.get("health_up"), FORMULA),
            self._value(_text(catalog, "label.health_add", "生命固定值"),
                        profile.get("health_add"), FORMULA),
            self._value(_text(catalog, "label.defense_base", "防御基础"),
                        profile.get("defense_base"), FORMULA),
            self._value(_text(catalog, "label.defense_up", "防御加成"),
                        profile.get("defense_up"), FORMULA),
            self._value(_text(catalog, "label.defense_add", "防御固定值"),
                        profile.get("defense_add"), FORMULA),
            self._value(_text(catalog, "label.defense_ignore", "防御忽略"),
                        profile.get("defense_ignore"), FORMULA),
            self._value(_text(catalog, "label.topple_limit", "倾陷上限"),
                        profile.get("topple_limit"), FORMULA),
            self._value(_text(catalog, "label.topple_reset", "倾陷恢复"),
                        profile.get("topple_reduce_reset"), FORMULA),
            self._value(
                _text(catalog, "label.attack_tier", "攻击档"),
                _text(
                    catalog, "value.attack_unavailable",
                    "不可用（schema v30 无攻击属性字段）",
                ),
                UNAVAILABLE,
                display_value=_text(catalog, "value.no_data", "暂无数据"),
            ),
        ]
        if variant_kind:
            values.append(self._value(
                _text(catalog, "label.profile_variant_kind", "画像档位类型"),
                variant_kind, FORMULA,
            ))
        for resistance in profile.get("resistances", ()):
            damage_type = str(resistance["damage_type"])
            values.append(self._value(
                _text(catalog, "label.resistance", "抗性 {damage_type}",
                      damage_type=damage_type),
                _text(
                    catalog, "value.resistance", "{base} / 免疫 {immunity}",
                    base=display_catalog_scalar(resistance.get("resistance_base")),
                    immunity=display_catalog_scalar(resistance.get("immunity")),
                ),
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
        catalog = self._text_catalog
        mode = "world_boss" if world_boss else "official_illustrated"
        entry = self._entry(
            _key("world_boss" if world_boss else "manual_monster", manual_id),
            domain="encounter" if world_boss else "monster",
            play_mode=mode,
            title_value=row.get("name_zh"),
            fallback=NAME_UNAVAILABLE,
            subtitle=enum_text(catalog, _PLAY_MODE_LABELS[mode]),
            primary_id=manual_id,
            secondary_id=str(row.get("enemy_type") or ""),
            resource_path=str(row.get("world_image_path") or row.get("image_path") or ""),
        )
        values = (
            self._value(_text(catalog, "label.formal_manual_id", "正式图鉴 ID"),
                        manual_id, copyable=True),
            self._localized_value(_text(catalog, "label.name_zh", "中文名"), row.get("name_zh")),
            self._value(_text(catalog, "label.enemy_type", "敌人类型"), row.get("enemy_type")),
            self._localized_value(_text(catalog, "label.place", "地区 / 位置"),
                                  row.get("place_zh")),
            self._value(_text(catalog, "label.trace_type", "追踪类型"), row.get("trace_type")),
        )
        sections = [CatalogSection(
            _text(catalog, "section.formal_identity", "正式身份"), values,
        )]
        if row.get("aliases"):
            sections.append(CatalogSection(_text(
                catalog, "section.formal_aliases", "正式别名",
            ), tuple(
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
                _text(
                    catalog, "section.template_binding", "模板绑定 · {binding_kind}",
                    binding_kind=binding["binding_kind"],
                ),
                (
                    self._value(
                        _text(catalog, "label.template_id", "模板 ID"),
                        binding.get("monster_template_name"), copyable=True,
                    ),
                    self._value(
                        _text(catalog, "label.binding_kind", "绑定类型"),
                        binding.get("binding_kind"),
                    ),
                ),
                _text(
                    catalog, "note.explicit_binding",
                    "这是静态库显式绑定，不是按中文名匹配。",
                ),
            ))
            for profile in binding.get("profiles", ()):
                relations.append(CatalogRelation(
                    _text(
                        catalog, "relation.view_template_profile",
                        "查看模板画像：{static_table}",
                        static_table=profile["static_table"],
                    ),
                    _key("profile_monster", profile["static_table"], profile["monster_id"]),
                    "explicit_template_binding",
                    _text(
                        catalog, "note.template_binding_relation",
                        "由 monster_template_binding 的正式模板名连接。",
                    ),
                ))
        if not world_boss and row.get("enemy_type") == "WeeklyBoss":
            relations.append(CatalogRelation(
                _text(catalog, "relation.view_world_boss", "查看异象追猎玩法条目"),
                _key("world_boss", manual_id),
                "official_enemy_type",
            ))
        sections.append(self._source_section(row.get("source")))
        notices = () if entry.localization_available else (
            _text(
                catalog, "notice.replacement_characters",
                "该记录的中文文本在发行静态库中已含替换字符，本页不修复或补猜。",
            ),
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
            subtitle=enum_text(self._text_catalog, _PLAY_MODE_LABELS["template_profile"]),
            primary_id=monster_id,
            secondary_id=static_table,
        )
        catalog = self._text_catalog
        sections = [CatalogSection(
            _text(catalog, "section.formal_template_record", "正式模板记录"), (
                self._value(_text(catalog, "label.static_table", "静态表"),
                            static_table, copyable=True),
                self._value(_text(catalog, "label.monster_template_id", "怪物模板 ID"),
                            monster_id, copyable=True),
                self._value(_text(catalog, "label.default_level", "默认等级"),
                            row.get("monster_level")),
                self._value("online_ratio_id", row.get("online_ratio_id"), copyable=True),
            ),
        )]
        default_profile = None
        if row.get("default_profile_set") and row.get("default_pack_id"):
            default_profile = self._queries.combat_profile(
                row["default_profile_set"], row["default_pack_id"]
            )
        sections.append(self._combat_profile_section(
            default_profile,
            level=row.get("monster_level"),
            title=_text(
                catalog, "section.default_combat_profile", "默认等价公式画像",
            ),
        ))
        for variant in row.get("variants", ()):
            sections.append(self._combat_profile_section(
                self._queries.combat_profile(variant["profile_set"], variant["pack_id"]),
                level=variant.get("threshold_level"),
                variant_kind=str(variant.get("variant_kind") or ""),
                title=_text(
                    catalog, "section.level_profile",
                    "等级画像 · 等级 {level}",
                    level=variant["threshold_level"],
                ),
            ))
        relations: list[CatalogRelation] = [CatalogRelation(
            _text(
                catalog, "relation.formal_manual", "正式图鉴：{manual_id}",
                manual_id=binding["monster_manual_id"],
            ),
            _key("manual_monster", binding["monster_manual_id"]),
            "explicit_template_binding",
            _text(
                catalog, "note.binding_type", "绑定类型：{binding_kind}",
                binding_kind=binding["binding_kind"],
            ),
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
                _text(
                    catalog, "relation.gameplay_reference",
                    "玩法引用：{label} · {title}",
                    label=enum_text(
                        catalog, _PLAY_MODE_LABELS.get(kind, kind),
                    ),
                    title=title,
                ),
                _key(kind, *parts),
                str(reference.get("relation_kind") or "exact_official_reference"),
                _text(
                    catalog, "note.reference_identity",
                    "来自正式模板 ID 或 Unreal 类路径对象名，不使用中文名。",
                ),
            ))
        sections.append(self._source_section(row.get("source")))
        notice = (
            _text(
                catalog, "notice.family_evidence_title",
                "标题使用同一正式 mon/boss 数字家族中全部可用名称证据的唯一结果；"
                "这只补充展示，不建立当前模板与图鉴的身份关系。",
            )
            if family_evidence_row else (
                _text(
                    catalog, "notice.template_identity",
                    "怪物模板 ID 是正式静态字段；只有显式 monster_template_binding "
                    "才建立与图鉴 ID 的身份关系。共用数值画像不证明身份。",
                )
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


    def _path_relations(self, class_path: object) -> list[CatalogRelation]:
        path = str(class_path or "").strip()
        if not path or "." not in path:
            return []
        object_name = path.rsplit(".", 1)[-1]
        if object_name.endswith("_C"):
            object_name = object_name[:-2]
        catalog = self._text_catalog
        relations = []
        for candidate in self._queries.template_profile_candidates(object_name):
            relations.append(CatalogRelation(
                _text(
                    catalog, "relation.class_path_profile",
                    "类路径对象的模板画像：{static_table}",
                    static_table=candidate["static_table"],
                ),
                _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                "exact_class_path_object",
                _text(
                    catalog, "note.class_path_identity",
                    "仅按正式类路径中的对象名精确连接，不使用中文名。",
                ),
            ))
        return relations


def provenance_label(
    provenance: str, *, text_catalog: TextCatalog | None = None,
) -> str:
    return {
        OFFICIAL: _text(text_catalog, "provenance.official", "官方静态事实"),
        FORMULA: _text(text_catalog, "provenance.formula", "等价公式画像"),
        DERIVED: _text(text_catalog, "provenance.derived", "项目派生 / 注解"),
        UNAVAILABLE: _text(text_catalog, "provenance.unavailable", "不可用"),
    }.get(provenance, provenance)


def play_mode_choices(*, text_catalog: TextCatalog | None = None) -> tuple[tuple[str, str], ...]:
    return (
        ("all", _text(text_catalog, "play_mode.all", "全部玩法")),
        *tuple(enum_text(text_catalog, _PLAY_MODE_LABELS).items()),
    )


def release_scope_choices(*, text_catalog: TextCatalog | None = None) -> tuple[tuple[str, str], ...]:
    return (
        ("all", _text(text_catalog, "release_scope.all", "全部期数")),
        ("current_next", _text(text_catalog, "release_scope.current_next", "当前 + 下一期")),
        ("current", _text(text_catalog, "release_scope.current", "仅当前期")),
        ("next", _text(text_catalog, "release_scope.next", "仅下一期")),
    )
