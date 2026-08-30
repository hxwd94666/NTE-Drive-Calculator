# 轨外之境关卡与单个刷怪成员的只读资料投影。
"""Build outer-realm catalog details without hiding unscheduled configurations."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from src.services.static_catalog_monster_display import (
    NAME_UNAVAILABLE,
    display_fight_stage,
)
from src.services.static_catalog_monster_models import (
    CatalogDetail,
    CatalogRelation,
    CatalogSection,
)


_RELEASE_LABELS = {
    "current": "当前期",
    "next": "下一期",
    "historical": "已结束",
    "scheduled": "待开放",
    "unscheduled": "未提供排期",
}


def _key(kind: str, *parts: object) -> str:
    return "|".join((kind, *(quote(str(part), safe="") for part in parts)))


def _unique_relations(relations: list[CatalogRelation]) -> tuple[CatalogRelation, ...]:
    return tuple(dict.fromkeys(relations))


class StaticCatalogOuterRealmDetailMixin:
    """Project exact level/member rows while keeping schedule optional."""

    _queries: Any
    _terminology_service: Any

    def _outer_detail(
        self, config_id: str, level_id: int, fight_stage: str,
    ) -> CatalogDetail | None:
        row = self._queries.outer_realm_encounter(config_id, level_id, fight_stage)
        if row is None:
            return None
        state = self._release_state(
            row.get("starts_at_mainland"), row.get("ends_at_mainland")
        )
        title = str(row.get("name_zh") or "").strip()
        if not title or "\ufffd" in title:
            title = NAME_UNAVAILABLE
        half_label = display_fight_stage(self._terminology_service, fight_stage)
        entry = self._entry(
            _key("outer_realm", config_id, level_id, fight_stage),
            domain="encounter", play_mode="outer_realm", title_value=title,
            fallback=NAME_UNAVAILABLE,
            subtitle=f"轨外之境 · {_RELEASE_LABELS[state]} · {half_label}",
            primary_id=config_id, secondary_id=fight_stage, release_state=state,
            secondary_label=half_label,
        )
        sections = [CatalogSection("正式期数与分区", (
            self._value("配置 ID", config_id, copyable=True),
            self._value("层", level_id),
            self._value(
                "上/下半场", fight_stage, copyable=True,
                display_value=half_label,
            ),
            self._value("大陆服开始", row.get("starts_at_mainland")),
            self._value("大陆服结束", row.get("ends_at_mainland")),
            self._value(
                "期数状态", _RELEASE_LABELS[state], "project_annotation",
            ),
        ), (
            "有正式排期时按大陆服时间判定；没有排期的配置仍展示其正式层、"
            "半场与怪物池，不推测开放时间。"
        ))]
        relations: list[CatalogRelation] = []
        for index, member in enumerate(row.get("members", ()), 1):
            section_title = f"刷怪槽位 {index}"
            sections.append(CatalogSection(section_title, (
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
                title=f"{section_title} 公式画像",
            ))
            relations.append(CatalogRelation(
                f"{section_title} 敌方档案",
                _key(
                    "outer_member", config_id, level_id, fight_stage,
                    member.get("spawn_ordinal"), member.get("monster_pool_id"),
                    member.get("monster_ordinal"),
                ),
                "exact_spawn_pool_member",
                "由正式关卡、半场、刷怪顺序、怪物池和怪物顺序共同定位。",
            ))
            relations.extend(self._path_relations(member.get("monster_class_path")))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(entry, tuple(sections), _unique_relations(relations))

    def _outer_member_detail(
        self,
        config_id: str,
        level_id: int,
        fight_stage: str,
        spawn_ordinal: int,
        monster_pool_id: str,
        monster_ordinal: int,
    ) -> CatalogDetail | None:
        row = self._queries.outer_realm_member(
            config_id, level_id, fight_stage, spawn_ordinal,
            monster_pool_id, monster_ordinal,
        )
        if row is None:
            return None
        path = str(row.get("monster_class_path") or "")
        object_name = path.rsplit(".", 1)[-1]
        if object_name.endswith("_C"):
            object_name = object_name[:-2]
        state = self._release_state(
            row.get("starts_at_mainland"), row.get("ends_at_mainland")
        )
        half_label = display_fight_stage(self._terminology_service, fight_stage)
        entry = self._entry(
            _key(
                "outer_member", config_id, level_id, fight_stage,
                spawn_ordinal, monster_pool_id, monster_ordinal,
            ),
            domain="monster", play_mode="outer_realm",
            title_value=row.get("monster_name_zh"), fallback=NAME_UNAVAILABLE,
            subtitle=f"轨外之境 · {_RELEASE_LABELS[state]} · {half_label}",
            primary_id=object_name or monster_pool_id,
            secondary_id=path, release_state=state,
            secondary_label=half_label,
        )
        sections = [CatalogSection("正式出场记录", (
            self._value("配置 ID", config_id, copyable=True),
            self._value("层", level_id),
            self._value("上/下半场", fight_stage, copyable=True, display_value=half_label),
            self._value("刷怪池 ID", monster_pool_id, copyable=True),
            self._value("刷怪顺序", spawn_ordinal),
            self._value("怪物顺序", monster_ordinal),
            self._value("怪物类路径", path, copyable=True),
            self._localized_value("怪物中文名", row.get("monster_name_zh")),
            self._value("数量", row.get("monster_count")),
            self._value("等级", row.get("monster_level")),
        ), "这是正式刷怪池成员，不用中文名反推怪物身份。")]
        sections.append(self._combat_profile_section(
            row.get("profile"), level=row.get("monster_level"),
            title="本次出场公式画像",
            note="由本刷怪成员的正式 profile_set + pack_id 解析。",
        ))
        sections.append(self._source_section(row.get("source")))
        return CatalogDetail(
            entry,
            tuple(sections),
            _unique_relations(self._path_relations(path)),
        )
