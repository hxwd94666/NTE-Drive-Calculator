# 材料/养成副本与高危委托的详情投影；只读静态查询结果，不推测缺失的静态身份。
from __future__ import annotations

from src.integrations.preferences.locale_files import enum_text
from src.services.static_catalog.monsters.catalog_support import (
    OFFICIAL,
    UNAVAILABLE,
    _PLAY_MODE_LABELS,
    _key,
    _text,
    unique_relations,
)
from src.services.static_catalog.monsters.display import NAME_UNAVAILABLE
from src.services.static_catalog.monsters.models import CatalogDetail, CatalogRelation, CatalogSection


class StaticCatalogActivityDetailMixin:
    """由 StaticCatalogMonsterService 组合；依赖其 _queries、_entry、_value 等投影工具。"""

    def _clone_detail(self, clone_id: str, ordinal: int) -> CatalogDetail | None:
        row = self._queries.clone_encounter(clone_id, ordinal)
        if row is None:
            return None
        entry = self._entry(
            _key("clone", clone_id, ordinal), domain="encounter", play_mode="clone",
            title_value=row.get("name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=_text(
                self._text_catalog, "entry.clone_subtitle",
                "{play_mode} · 难度 {ordinal}",
                play_mode=enum_text(
                    self._text_catalog, _PLAY_MODE_LABELS["clone"],
                ),
                ordinal=ordinal,
            ),
            primary_id=clone_id, secondary_id=str(row.get("clone_type") or ""),
        )
        catalog = self._text_catalog
        sections = [CatalogSection(
            _text(catalog, "section.clone_config", "正式副本配置"), (
                self._value(_text(catalog, "label.clone_id", "副本 ID"),
                            clone_id, copyable=True),
                self._value(
                    _text(catalog, "label.clone_type", "副本类型"), row.get("clone_type"),
                    OFFICIAL if row.get("clone_type") else UNAVAILABLE,
                    copyable=bool(row.get("clone_type")),
                ),
                self._localized_value(_text(catalog, "label.category", "类目"),
                                      row.get("category_name_zh")),
                self._value(
                    _text(catalog, "label.adventure_manual", "冒险手册展示"),
                    bool(row.get("show_in_adventure")) if row.get("show_in_adventure") is not None else None,
                    OFFICIAL if row.get("show_in_adventure") is not None else UNAVAILABLE,
                ),
                self._value(
                    _text(catalog, "label.cross_scene", "跨场景"),
                    bool(row.get("cross_scene")) if row.get("cross_scene") is not None else None,
                    OFFICIAL if row.get("cross_scene") is not None else UNAVAILABLE,
                ),
                self._value(_text(catalog, "label.difficulty_ordinal", "难度序号"), ordinal),
                self._value(_text(catalog, "label.difficulty_level", "难度等级"),
                            row.get("difficulty_level")),
                self._value(_text(catalog, "label.team_level", "队伍等级"),
                            row.get("team_level")),
                self._value(_text(catalog, "label.stamina", "体力"),
                            row.get("stamina_cost")),
                self._value(_text(catalog, "label.spawn_config_id", "刷怪配置 ID"),
                            row.get("spawn_id"), copyable=True),
                self._value(_text(catalog, "label.kill_time_limit", "击杀时限"),
                            row.get("kill_monster_time_limit")),
            ),
        )]
        sections.append(self._gameplay.drop_section(row.get("drop_projection")))
        relations: list[CatalogRelation] = []
        members = row.get("members", ())
        if not members:
            sections.append(CatalogSection(
                _text(catalog, "section.spawn_slots", "刷怪槽位"), (
                    self._value(
                        _text(catalog, "label.formal_monster_pool", "正式怪物池"),
                        None, UNAVAILABLE,
                    ),
                ),
            ))
        for index, member in enumerate(members, 1):
            sections.append(CatalogSection(_text(
                catalog, "section.spawn_slot", "刷怪槽位 {index}", index=index,
            ), (
                self._value(_text(catalog, "label.wave", "波次"),
                            member.get("wave_ordinal")),
                self._value(_text(catalog, "label.template_path", "模板路径"),
                            member.get("monster_template_path"), copyable=True),
                self._value(_text(catalog, "label.template_id", "模板 ID"),
                            member.get("monster_template_name"), copyable=True),
                self._value(_text(catalog, "label.count", "数量"),
                            member.get("monster_count")),
            )))
            profiles = self._queries.template_profile_candidates(
                member.get("monster_template_name") or ""
            )
            for candidate in profiles:
                relations.append(CatalogRelation(
                    _text(
                        catalog, "relation.spawn_slot_profile",
                        "刷怪槽位 {index} 模板画像：{static_table}",
                        index=index,
                        static_table=candidate["static_table"],
                    ),
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
            subtitle=_text(
                self._text_catalog, "entry.high_risk_subtitle",
                "高危委托 · 难度 {difficulty}", difficulty=difficulty,
            ), primary_id=commission_id,
            secondary_id=str(row.get("monster_pool_id") or ""),
        )
        catalog = self._text_catalog
        sections = [CatalogSection(
            _text(catalog, "section.commission_config", "正式委托配置"), (
                self._value(_text(catalog, "label.commission_id", "委托 ID"),
                            commission_id, copyable=True),
                self._localized_value(_text(catalog, "label.commission_name", "委托名"),
                                      row.get("name_zh")),
                self._value(_text(catalog, "label.difficulty", "难度"), difficulty),
                self._value(_text(catalog, "label.recommended_level", "推荐角色等级"),
                            row.get("recommended_character_level")),
                self._value(_text(catalog, "label.scene_id", "场景 ID"),
                            row.get("scene_data_id"), copyable=True),
                self._value(_text(catalog, "label.per_difficulty_pool", "逐难度怪物池"),
                            row.get("monster_pool_id"), copyable=True),
                self._value(_text(catalog, "label.fallback_pool", "通用回退池"),
                            row.get("fallback_monster_pool_id"), copyable=True),
            ),
        )]
        relations: list[CatalogRelation] = []
        members = row.get("members", ())
        if not row.get("monster_pool_id"):
            sections.append(CatalogSection(
                _text(
                    catalog, "section.per_difficulty_pool",
                    "逐难度正式怪物池",
                ), (
                    self._value(
                        _text(catalog, "label.monster_pool", "怪物池"),
                        _text(
                            catalog, "value.pool_unavailable",
                            "不可用（本难度只有通用回退池）",
                        ),
                        UNAVAILABLE,
                    ),
                ),
            ))
        for index, member in enumerate(members, 1):
            sections.append(CatalogSection(_text(
                catalog, "section.pool_member", "怪物池成员 {index}", index=index,
            ), (
                self._value(_text(catalog, "label.class_path", "类路径"),
                            member.get("monster_class_path"), copyable=True),
                self._value(_text(catalog, "label.template_id", "模板 ID"),
                            member.get("monster_template_name"), copyable=True),
                self._value(_text(catalog, "label.count", "数量"),
                            member.get("monster_count")),
                self._value(_text(catalog, "label.configured_level", "配置等级"),
                            member.get("configured_monster_level")),
                self._value(_text(catalog, "label.attribute_id", "属性 ID"),
                            member.get("attribute_id"), copyable=True),
            )))
            for candidate in self._queries.template_profile_candidates(
                member.get("monster_template_name") or ""
            ):
                relations.append(CatalogRelation(
                    _text(
                        catalog, "relation.pool_member_profile",
                        "怪物池成员 {index} 模板画像：{static_table}",
                        index=index,
                        static_table=candidate["static_table"],
                    ),
                    _key("profile_monster", candidate["static_table"], candidate["monster_id"]),
                    "exact_official_template_id",
                ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), unique_relations(relations))
