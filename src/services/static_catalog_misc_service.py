# 将装备、技能效果、资源关系和来源追溯投影为 Qt 无关的资料库 DTO。
"""Qt-free projections for the B-domain static game catalog."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from src.services.static_catalog_misc_models import (
    CatalogDetail,
    CatalogDomain,
    CatalogField,
    CatalogRelation,
    CatalogRelationPage,
    CatalogSearchItem,
    CatalogSearchPage,
    CatalogSection,
    ORIGIN_ANNOTATION,
    ORIGIN_DERIVED,
    ORIGIN_FORMAL,
    ORIGIN_SOURCE,
    SourceTrace,
    StaticCatalogReleaseMetadata,
)
from src.services.static_catalog_misc_metadata import BASE_FIELD_KEYS
from src.storage.sqlite.static_catalog_misc_queries import StaticCatalogMiscDao


_ORIGIN_LABELS = {
    ORIGIN_FORMAL: "正式静态",
    ORIGIN_ANNOTATION: "项目注解",
    ORIGIN_DERIVED: "派生显示值",
    ORIGIN_SOURCE: "来源元数据",
}


_DOMAIN_PRESENTATION = {
    "equipment": (
        "装备与养成",
        "空幕、驱动、卡带、套装、形状、属性、强化曲线、图纸与毕业模板",
    ),
    "skills": ("技能与伤害", "Gameplay Ability、技能说明、等级提示与正式伤害项"),
    "effects": ("Buff 与效果", "Gameplay Effect、Buff、modifier、触发关系与 Gameplay Tag"),
    "assets": ("资源与动画", "Blueprint、Montage、Section、Notify 与资源关系"),
    "sources": ("来源追溯", "保留的来源路径、文件哈希、行 key 与内容哈希"),
}

_ENTITY_DOMAIN = {
    "equipment_item": "equipment",
    "equipment_suit": "equipment",
    "equipment_shape": "equipment",
    "equipment_attribute": "equipment",
    "equipment_curve": "equipment",
    "equipment_buff_curve": "equipment",
    "equipment_modify_pack": "equipment",
    "equipment_plan": "equipment",
    "graduation_template": "equipment",
    "gameplay_ability": "skills",
    "skill_damage": "skills",
    "gameplay_effect": "effects",
    "buff": "effects",
    "combat_effect": "effects",
    "combat_curve": "effects",
    "combat_level_curve": "effects",
    "reaction": "effects",
    "combat_constant": "effects",
    "gameplay_tag": "effects",
    "roguelike_modifier": "effects",
    "blueprint": "assets",
    "montage": "assets",
    "source_file": "sources",
    "source_row": "sources",
}

_ENTITY_ORIGIN = {
    "graduation_template": ORIGIN_DERIVED,
    "combat_effect": ORIGIN_ANNOTATION,
    "source_file": ORIGIN_SOURCE,
    "source_row": ORIGIN_SOURCE,
}

_COPY_BY_KEY = {
    "item_id": "official_id",
    "suit_id": "official_id",
    "shape_id": "official_id",
    "attribute_id": "official_id",
    "curve_id": "official_id",
    "modify_pack_id": "official_id",
    "character_id": "official_id",
    "ability_id": "ga_id",
    "damage_id": "ge_id",
    "gameplay_effect_id": "ge_id",
    "definition_id": "buff_key",
    "effect_definition_id": "buff_key",
    "tag_name": "gameplay_tag",
    "asset_path": "resource_path",
    "class_path": "resource_path",
    "gameplay_ability_path": "resource_path",
    "icon_path": "resource_path",
    "plan_icon_path": "resource_path",
    "background_path": "resource_path",
    "character_image_path": "resource_path",
    "extended_icon_path": "resource_path",
    "buff_object_path": "resource_path",
    "calculation_asset_path": "resource_path",
    "target_effect_asset_path": "resource_path",
    "source_asset_path": "resource_path",
    "montage_asset_path": "resource_path",
    "linked_animation_asset_path": "resource_path",
    "curve_table_asset_path": "resource_path",
    "target_asset_path": "resource_path",
    "target_object_path": "resource_path",
    "effect_asset_path": "resource_path",
    "target_type_asset_path": "resource_path",
    "montage_object_path": "resource_path",
    "notify_object_path": "resource_path",
    "relative_path": "resource_path",
    "sha256": "sha256",
    "content_sha256": "sha256",
    "source_file_sha256": "sha256",
}

_FIELD_LABELS = {
    "item_id": "装备正式 ID",
    "kind": "装备类型",
    "quality": "品质",
    "name_zh": "中文名",
    "geometry_id": "形状 ID",
    "geometry_enum": "形状枚举",
    "grid_count": "占用格数",
    "suit_id": "套装正式 ID",
    "suit_type_enum": "套装枚举",
    "max_level": "最高等级",
    "strength_pack_id": "强化包 ID",
    "is_guide_item": "引导物品",
    "shape_id": "形状正式 ID",
    "cell_count": "格数",
    "first_grid_delta_x": "首格 X 偏移",
    "first_grid_delta_y": "首格 Y 偏移",
    "attribute_id": "属性正式 ID",
    "display_name_zh": "显示名",
    "filter_name_zh": "筛选名",
    "random_attribute_name_zh": "随机词条名",
    "attribute_type": "属性类型",
    "show_percent": "百分比显示",
    "score": "官方评分参数",
    "curve_id": "曲线正式 ID",
    "interpolation_mode": "插值方式",
    "default_value": "默认值",
    "modify_pack_id": "修改包正式 ID",
    "conditions": "生效条件",
    "character_id": "角色正式 ID",
    "core_item_id": "卡带 ID",
    "core_level": "卡带等级",
    "module_level": "驱动等级",
    "reference_score": "参考分",
    "fork_id": "弧盘 ID",
    "fork_level": "弧盘等级",
    "fork_refinement_level": "弧盘精炼",
    "core_suit_id": "套装 ID",
    "core_main_property_id": "卡带主词条 ID",
    "drive_area": "驱动面积",
    "extra_shape_count": "额外形状数",
    "benchmark_damage": "模板基准伤害",
    "source_kind": "生成来源",
    "generated_at_utc": "生成时间",
    "ability_id": "GA 正式 ID",
    "gameplay_ability_path": "GA 资源路径",
    "is_stolen": "可偷取技能",
    "damage_id": "伤害项 / GE 正式 ID",
    "damage_type": "伤害类型",
    "damage_source_category": "伤害来源分类",
    "charge_add": "充能增加",
    "unbal_value": "倾陷值",
    "heterochrome_add": "异能增加",
    "fixed_crit_rate": "固定暴击率",
    "atk_rate_base": "攻击倍率数组",
    "def_rate_base": "防御倍率数组",
    "hp_rate_base": "生命倍率数组",
    "story_balance_ge_rate": "剧情平衡倍率",
    "attack_break_level": "破坏等级",
    "override_breakable_damage": "覆盖可破坏物伤害",
    "breakable_damage": "可破坏物伤害",
    "override_breakable_impulse": "覆盖可破坏物冲量",
    "breakable_impulse": "可破坏物冲量",
    "override_vehicle_breakable_impulse": "覆盖载具可破坏冲量",
    "vehicle_breakable_impulse": "载具可破坏冲量",
    "ability_relation_status": "来源 GA 关系状态",
    "same_name_gameplay_effect_relation_status": "同名 GE 关系状态",
    "modifier_atk_rate_base_coefficient": "项目倍率修正系数",
    "gameplay_effect_index": "GE 正式索引",
    "gameplay_effect_id": "GE 正式 ID",
    "class_path": "GE 类路径",
    "definition_id": "Buff key",
    "definition_kind": "定义类型",
    "owner_character_id": "所属角色 ID",
    "duration_policy": "持续策略",
    "duration_magnitude": "持续时间证据",
    "period": "周期证据",
    "stacking_type": "叠层类型",
    "stack_limit_count": "叠层上限",
    "effect_definition_id": "效果定义 key",
    "owner_kind": "所有者类型",
    "owner_id": "所有者 ID",
    "effect_kind": "效果类型",
    "activation_kind": "激活方式",
    "description_zh": "说明",
    "formula_version": "项目公式版本",
    "curve_table_asset_path": "曲线表资源路径",
    "damage_kind": "伤害类别",
    "reaction_type": "反应类型",
    "source_effect_id": "来源效果 ID",
    "mapping_status": "映射状态",
    "element_type_1": "元素一",
    "element_type_2": "元素二",
    "default_damage_effect_id": "默认伤害项 ID",
    "constant_id": "常量 ID",
    "source_time": "输入值",
    "value": "数值",
    "unit": "单位",
    "tag_name": "Gameplay Tag",
    "modifier_id": "属性包正式 ID",
    "ordinal": "序号",
    "property_id": "属性正式 ID",
    "modifier_operation": "修改运算",
    "property_value": "属性值",
    "sort_key": "排序键",
    "owner_resolution_status": "归属解析状态",
    "asset_path": "资源路径",
    "source_asset_path": "来源资源路径",
    "property_path": "属性路径",
    "asset_name": "资源名",
    "asset_type": "资源类型",
    "asset_kind": "资源领域",
    "duration_seconds": "时长（秒）",
    "blend_in_seconds": "混入（秒）",
    "blend_out_seconds": "混出（秒）",
    "frame_rate_numerator": "帧率分子",
    "frame_rate_denominator": "帧率分母",
}

def _display(value: object) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    return str(value)


def _origin_for(entity_kind: str) -> str:
    return _ENTITY_ORIGIN.get(entity_kind, ORIGIN_FORMAL)


def _field(
    label: str,
    value: object,
    *,
    origin: str,
    copy_kind: str | None = None,
) -> CatalogField:
    return CatalogField(
        label=label,
        value=_display(value),
        origin_kind=origin,
        origin_label=_ORIGIN_LABELS[origin],
        copy_kind=copy_kind,
    )


class StaticCatalogMiscService:
    """Own B-domain information architecture while DAO owns every SQL statement."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        manifest_path: str | Path | None = None,
        dao_factory: Callable[..., StaticCatalogMiscDao] = StaticCatalogMiscDao,
    ) -> None:
        self.database_path = Path(database_path)
        self.manifest_path = Path(manifest_path) if manifest_path is not None else None
        self.dao_factory = dao_factory

    def domains(self) -> tuple[CatalogDomain, ...]:
        with self.dao_factory(self.database_path) as dao:
            counts = dao.catalog_domain_counts()
        return tuple(
            CatalogDomain(key, title, description, counts.get(key, 0))
            for key, (title, description) in _DOMAIN_PRESENTATION.items()
        )

    def search(
        self,
        domain_key: str,
        query: str = "",
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogSearchPage:
        with self.dao_factory(self.database_path) as dao:
            page = dao.search_catalog_entries(
                domain_key, query, limit=limit, offset=offset
            )
        items = tuple(self._search_item(row) for row in page["items"])
        return CatalogSearchPage(
            domain_key=str(page["domain_key"]),
            query=str(page["query"]),
            offset=int(page["offset"]),
            limit=int(page["limit"]),
            total=int(page["total"]),
            items=items,
        )

    def detail(self, entity_kind: str, entity_key: str) -> CatalogDetail | SourceTrace:
        domain = _ENTITY_DOMAIN.get(str(entity_kind))
        if domain is None:
            raise ValueError(f"不支持的资料库实体：{entity_kind!r}")
        if entity_kind == "source_file":
            return self.source_trace(source_file_id=int(entity_key))
        if entity_kind == "source_row":
            return self.source_trace(source_row_id=int(entity_key))
        with self.dao_factory(self.database_path) as dao:
            if domain == "equipment":
                raw = dao.get_equipment_catalog_detail(entity_kind, entity_key)
            elif domain in {"skills", "effects"}:
                raw = dao.get_effect_catalog_detail(entity_kind, entity_key)
            elif domain == "assets":
                raw = dao.get_asset_catalog_detail(entity_kind, entity_key)
            else:
                raw = None
        if raw is None:
            raise LookupError(f"资料库中不存在 {entity_kind}:{entity_key}")
        return self._detail_from_raw(entity_kind, entity_key, raw)

    def source_trace(
        self,
        *,
        source_row_id: int | None = None,
        source_file_id: int | None = None,
    ) -> SourceTrace:
        with self.dao_factory(self.database_path) as dao:
            raw = dao.get_source_trace(
                source_row_id=source_row_id, source_file_id=source_file_id
            )
        if raw is None:
            raise LookupError("找不到对应来源记录")
        omitted = self.release_metadata().source_payloads_omitted
        payload_present = bool(raw["payload_present"])
        if omitted:
            explanation = (
                "发行包省略原始 payload；这里只展示保留的来源路径、行 key 与哈希。"
            )
        elif payload_present:
            explanation = "当前构建保留了该来源行 payload；本资料库仍只展示来源元数据。"
        else:
            explanation = "当前来源行没有可展示的 payload，只保留来源元数据。"
        return SourceTrace(
            source_file_id=int(raw["source_file_id"]),
            relative_path=str(raw["relative_path"]),
            source_file_sha256=str(raw["source_file_sha256"]),
            declared_row_count=int(raw["row_count"]),
            source_row_id=(
                int(raw["source_row_id"]) if raw.get("source_row_id") is not None else None
            ),
            row_key=str(raw["row_key"]) if raw.get("row_key") is not None else None,
            content_sha256=(
                str(raw["content_sha256"])
                if raw.get("content_sha256") is not None
                else None
            ),
            payload_present=payload_present,
            payloads_omitted=omitted,
            explanation=explanation,
        )

    def source_rows(
        self,
        source_file_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogRelationPage:
        with self.dao_factory(self.database_path) as dao:
            page = dao.list_source_file_rows(
                source_file_id, limit=limit, offset=offset
            )
        rows = tuple(
            CatalogSection(
                title=str(row["row_key"]),
                fields=(
                    _field(
                        "来源行 ID", row["source_row_id"], origin=ORIGIN_SOURCE,
                        copy_kind="official_id",
                    ),
                    _field(
                        "内容 SHA-256", row["content_sha256"], origin=ORIGIN_SOURCE,
                        copy_kind="sha256",
                    ),
                ),
            )
            for row in page["items"]
        )
        return CatalogRelationPage(
            relation_kind="source_rows",
            offset=int(page["offset"]),
            limit=int(page["limit"]),
            total=int(page["total"]),
            rows=rows,
        )

    def asset_relations(
        self,
        entity_kind: str,
        entity_key: str,
        relation_kind: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> CatalogRelationPage:
        with self.dao_factory(self.database_path) as dao:
            page = dao.list_asset_relations(
                entity_kind,
                entity_key,
                relation_kind,
                limit=limit,
                offset=offset,
            )
        return CatalogRelationPage(
            relation_kind=relation_kind,
            offset=int(page["offset"]),
            limit=int(page["limit"]),
            total=int(page["total"]),
            rows=tuple(
                self._mapping_section(f"{relation_kind} #{offset + index + 1}", row)
                for index, row in enumerate(page["items"])
            ),
        )

    def release_metadata(self) -> StaticCatalogReleaseMetadata:
        if self.manifest_path is None:
            raise ValueError("未提供静态发行 manifest 路径")
        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        database = raw.get("database") or {}
        build_tool = raw.get("build_tool") or {}
        return StaticCatalogReleaseMetadata(
            dataset_id=str(database.get("dataset_id") or ""),
            schema_version=int(database.get("schema_version") or 0),
            importer_version=int(build_tool.get("importer_version") or 0),
            generated_at_utc=str(database.get("generated_at_utc") or ""),
            database_sha256=str(database.get("sha256") or ""),
            source_payloads_omitted=bool(database.get("source_payloads_omitted")),
        )

    @staticmethod
    def _search_item(row: Mapping[str, Any]) -> CatalogSearchItem:
        entity_kind = str(row["entity_kind"])
        origin = _origin_for(entity_kind)
        return CatalogSearchItem(
            entity_kind=entity_kind,
            entity_key=str(row["entity_key"]),
            title=str(row["title"]),
            subtitle=str(row["subtitle"]),
            origin_kind=origin,
            origin_label=_ORIGIN_LABELS[origin],
            source_row_id=(
                int(row["source_row_id"]) if row.get("source_row_id") is not None else None
            ),
            source_file_id=(
                int(row["source_file_id"])
                if row.get("source_file_id") is not None
                else None
            ),
        )

    def _detail_from_raw(
        self,
        entity_kind: str,
        entity_key: str,
        raw: Mapping[str, Any],
    ) -> CatalogDetail:
        origin = _origin_for(entity_kind)
        relation_counts = dict(raw.get("relation_counts") or {})
        if entity_kind == "montage" and raw.get("notify_count") is not None:
            relation_counts["notifies"] = int(raw["notify_count"])
        fields = tuple(
            _field(
                _FIELD_LABELS.get(key, key),
                raw.get(key),
                origin=self._field_origin(entity_kind, key),
                copy_kind=_COPY_BY_KEY.get(key),
            )
            for key in BASE_FIELD_KEYS[entity_kind]
            if key in raw
        )
        sections: list[CatalogSection] = [CatalogSection("基本信息", fields)]
        relations = list(self._relations(entity_kind, raw))
        for key, value in raw.items():
            if key in BASE_FIELD_KEYS[entity_kind] or key in {
                "source_row_id", "source_file_id", "relation_counts",
            }:
                continue
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
                sections.extend(self._sequence_sections(entity_kind, key, value))
            elif isinstance(value, Mapping):
                sections.append(self._mapping_section(self._section_title(key), value, origin))
        title, subtitle = self._detail_title(entity_kind, entity_key, raw)
        if relation_counts:
            sections.append(
                self._mapping_section("分页关系规模", relation_counts, ORIGIN_FORMAL)
            )
        return CatalogDetail(
            entity_kind=entity_kind,
            entity_key=str(entity_key),
            title=title,
            subtitle=subtitle,
            origin_kind=origin,
            origin_label=_ORIGIN_LABELS[origin],
            sections=tuple(sections),
            relations=tuple(relations),
            source_row_id=(
                int(raw["source_row_id"]) if raw.get("source_row_id") is not None else None
            ),
            source_file_id=(
                int(raw["source_file_id"])
                if raw.get("source_file_id") is not None
                else None
            ),
        )

    @staticmethod
    def _field_origin(entity_kind: str, key: str) -> str:
        if entity_kind == "graduation_template":
            return ORIGIN_DERIVED
        if entity_kind == "combat_effect":
            return ORIGIN_ANNOTATION
        if key.endswith("_relation_status") or key == "owner_resolution_status":
            return ORIGIN_DERIVED
        if key in {"modifier_atk_rate_base_coefficient", "formula_version"}:
            return ORIGIN_ANNOTATION
        return ORIGIN_FORMAL

    def _sequence_sections(
        self,
        entity_kind: str,
        key: str,
        values: Sequence[Any],
    ) -> list[CatalogSection]:
        title = self._section_title(key)
        if not values:
            return []
        origin = self._field_origin(entity_kind, key)
        if all(isinstance(value, Mapping) for value in values):
            return [
                self._mapping_section(f"{title} #{index + 1}", value, origin)
                for index, value in enumerate(values)
            ]
        return [
            CatalogSection(
                title,
                tuple(
                    _field(f"#{index + 1}", value, origin=origin)
                    for index, value in enumerate(values)
                ),
            )
        ]

    @staticmethod
    def _mapping_section(
        title: str,
        values: Mapping[str, Any],
        origin: str = ORIGIN_FORMAL,
    ) -> CatalogSection:
        return CatalogSection(
            title=title,
            fields=tuple(
                _field(
                    _FIELD_LABELS.get(key, key),
                    value,
                    origin=origin,
                    copy_kind=_COPY_BY_KEY.get(key),
                )
                for key, value in values.items()
            ),
        )

    @staticmethod
    def _section_title(key: str) -> str:
        return {
            "strength_levels": "强化消耗",
            "required_shapes": "套装要求形状",
            "effects": "套装效果",
            "cells": "形状格位",
            "curves": "关联强化曲线",
            "points": "曲线点",
            "modifiers": "Modifier",
            "descriptions": "技能说明",
            "level_hints": "等级提示",
            "tags": "Gameplay Tag",
            "triggers": "触发效果",
            "buff_links": "Buff / GE 关系",
            "sections": "Montage Section",
            "profile": "冻结计算档案",
            "equipment": "毕业装备",
            "parameters": "项目结构化参数",
            "properties": "属性 Modifier",
        }.get(key, key)

    @staticmethod
    def _detail_title(
        entity_kind: str,
        entity_key: str,
        raw: Mapping[str, Any],
    ) -> tuple[str, str]:
        title_keys = {
            "equipment_item": "name_zh",
            "equipment_suit": "name_zh",
            "equipment_attribute": "display_name_zh",
            "equipment_plan": "character_name_zh",
            "graduation_template": "character_name_zh",
            "gameplay_ability": "name_zh",
            "skill_damage": "damage_id",
            "gameplay_effect": "gameplay_effect_id",
            "buff": "definition_id",
            "combat_effect": "effect_definition_id",
            "combat_curve": "curve_id",
            "combat_level_curve": "curve_id",
            "reaction": "reaction_type",
            "combat_constant": "constant_id",
            "gameplay_tag": "tag_name",
            "roguelike_modifier": "modifier_id",
            "blueprint": "asset_name",
            "montage": "asset_path",
        }
        title = str(raw.get(title_keys.get(entity_kind, "")) or entity_key)
        subtitle = {
            "equipment_item": "装备模板",
            "equipment_suit": "卡带套装",
            "equipment_shape": "驱动形状",
            "equipment_attribute": "装备属性目录",
            "equipment_curve": "装备主属性强化曲线",
            "equipment_buff_curve": "装备效果曲线",
            "equipment_modify_pack": "装备属性修改包",
            "equipment_plan": "官方装备图纸",
            "graduation_template": "项目派生毕业模板",
            "gameplay_ability": "Gameplay Ability",
            "skill_damage": "正式技能伤害项",
            "gameplay_effect": "Gameplay Effect",
            "buff": "Buff 定义",
            "combat_effect": "项目结构化效果注解",
            "combat_curve": "正式战斗曲线",
            "combat_level_curve": "正式等级曲线",
            "reaction": "正式反应定义",
            "combat_constant": "正式战斗常量",
            "gameplay_tag": "Gameplay Tag",
            "roguelike_modifier": "正式玩法属性包",
            "blueprint": "Blueprint 资源",
            "montage": "Montage 动画",
        }[entity_kind]
        return title, subtitle

    @staticmethod
    def _relations(
        entity_kind: str,
        raw: Mapping[str, Any],
    ) -> tuple[CatalogRelation, ...]:
        relations: list[CatalogRelation] = []

        def add(label: str, kind: str, key: object, title: object | None = None) -> None:
            if key not in (None, "", "None"):
                relations.append(
                    CatalogRelation(label, kind, str(key), str(title or key))
                )

        if entity_kind == "equipment_item":
            add("查看套装", "equipment_suit", raw.get("suit_id"))
            add("查看形状", "equipment_shape", raw.get("geometry_id"))
        elif entity_kind == "equipment_suit":
            for effect in raw.get("effects") or ():
                add("查看套装 Buff", "buff", effect.get("buff_object_path"))
                add(
                    "查看属性修改包",
                    "equipment_modify_pack",
                    effect.get("modify_pack_id"),
                )
            for shape in raw.get("required_shapes") or ():
                add("查看要求形状", "equipment_shape", shape.get("shape_id"))
        elif entity_kind == "equipment_plan":
            add("查看卡带", "equipment_item", raw.get("core_item_id"), raw.get("core_name_zh"))
            for item_id in raw.get("module_item_ids") or ():
                add("查看驱动", "equipment_item", item_id)
        elif entity_kind == "graduation_template":
            add("查看弧盘", "fork", raw.get("fork_id"))
            add("查看套装", "equipment_suit", raw.get("core_suit_id"))
            add("查看主词条", "equipment_attribute", raw.get("core_main_property_id"))
        elif entity_kind == "gameplay_ability":
            add("查看 GA 资源", "blueprint", raw.get("gameplay_ability_path"))
            for hint in raw.get("level_hints") or ():
                for effect_id in hint.get("damage_effect_ids") or ():
                    add("查看伤害项", "skill_damage", effect_id)
                for field in ("defense_effect_ids", "health_effect_ids"):
                    for effect_id in hint.get(field) or ():
                        add("查看 GE", "gameplay_effect", effect_id)
        elif entity_kind == "skill_damage":
            if raw.get("ability_relation_status") == "available":
                add("查看来源 GA", "gameplay_ability", raw.get("ability_id"))
            if raw.get("same_name_gameplay_effect_relation_status") == "available":
                add("查看同名 GE", "gameplay_effect", raw.get("damage_id"))
        elif entity_kind == "gameplay_effect":
            add("查看 GE 资源", "blueprint", raw.get("asset_path"))
        elif entity_kind == "buff":
            add("查看 Buff 资源", "blueprint", raw.get("asset_path"))
            for modifier in raw.get("modifiers") or ():
                add("查看 Calculation", "blueprint", modifier.get("calculation_asset_path"))
            for trigger in raw.get("triggers") or ():
                add("查看触发目标", "buff", trigger.get("target_effect_asset_path"))
        elif entity_kind == "combat_effect":
            for link in raw.get("buff_links") or ():
                if bool(link.get("target_available")):
                    add("查看 Buff / GE", "buff", link.get("target_asset_path"))
        elif entity_kind == "combat_level_curve":
            add("查看来源 GE", "gameplay_effect", raw.get("source_effect_id"))
        elif entity_kind == "reaction":
            add("查看默认伤害项", "skill_damage", raw.get("default_damage_effect_id"))
        elif entity_kind == "gameplay_tag":
            add("查看来源 Blueprint", "blueprint", raw.get("source_asset_path"))
        return tuple(relations)
