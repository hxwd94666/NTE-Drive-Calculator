# 将发行静态库角色数据投影为 Qt 无关、不可变的资料库 DTO。
"""Character-domain projections for the read-only game catalog."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

from src.services.static_catalog_character_models import (
    AwakeningEffect,
    BreakthroughStage,
    BuildProperty,
    CatalogDataset,
    CatalogGap,
    CatalogSource,
    CharacterDetail,
    CharacterEquipmentPlan,
    CharacterPage,
    CharacterProgressionProfile,
    CharacterShapeBonus,
    CharacterSkill,
    CharacterSummary,
    CharacterWeightRecommendation,
    CombatLink,
    CombatLinkPage,
    CostItem,
    CultivationGuide,
    CultivationStage,
    GraduationTemplate,
    GrowthPage,
    GrowthPoint,
    LikeabilityBonus,
    LikeabilityProperty,
    SkillDescription,
    SkillDamageItem,
    SkillLevel,
    SkillLevelBonus,
    SkillLevelHint,
    StaticCatalogProjectionError,
    StructuredEffectField,
    EquipmentPlanModule,
)
from src.services.static_catalog_character_passive_service import (
    StaticCatalogCharacterPassiveService,
)
from src.services.static_catalog_character_progression import (
    project_character_progression,
)


_ELEMENT_LABELS = {
    "CHAOS": "暗",
    "COSMOS": "光",
    "INCANTATION": "咒",
    "LAKSHANA": "相",
    "NATURE": "灵",
    "PSYCHE": "魂",
    "PSYCHICALLY": "心灵",
}


class CharacterCatalogQueries(Protocol):
    def character_catalog_metadata(self) -> dict[str, Any]: ...
    def count_catalog_characters(self, query: str = "") -> int: ...
    def list_catalog_characters(
        self, *, query: str = "", limit: int = 50, offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def get_catalog_character(self, character_id: int) -> dict[str, Any] | None: ...
    def count_catalog_growth_points(self, character_id: int) -> int: ...
    def list_catalog_growth_points(
        self, character_id: int, *, limit: int = 40, offset: int = 0,
    ) -> list[dict[str, Any]]: ...
    def list_catalog_breakthrough_points(self, character_id: int) -> list[dict[str, Any]]: ...
    def get_catalog_character_progression(
        self, character_id: int,
    ) -> dict[str, Any] | None: ...
    def get_catalog_likeability(self, character_id: int) -> dict[str, Any] | None: ...
    def list_catalog_awakenings(self, character_id: int) -> list[dict[str, Any]]: ...
    def list_catalog_skills(self, character_id: int) -> list[dict[str, Any]]: ...
    def list_catalog_ability_details(
        self, ability_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]: ...
    def get_catalog_cultivation(self, character_id: int) -> dict[str, Any] | None: ...
    def get_catalog_graduation(self, character_id: int) -> dict[str, Any] | None: ...
    def get_catalog_equipment_plan(self, character_id: int) -> dict[str, Any] | None: ...
    def get_catalog_character_shape_bonus(self, character_id: int) -> dict[str, Any] | None: ...
    def get_catalog_character_weights(self, character_id: int) -> dict[str, Any] | None: ...
    def count_catalog_combat_links(self, character_id: int) -> int: ...
    def list_catalog_combat_links(
        self, character_id: int, *, limit: int = 100, offset: int = 0,
    ) -> list[dict[str, Any]]: ...


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: object) -> int | None:
    return None if value is None else int(str(value))


def _element_label(value: object) -> str:
    token = str(value or "").rsplit("_", 1)[-1]
    return _ELEMENT_LABELS.get(token, token or "未知")


def _loads(value: object, *, context: str) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError) as exc:
        raise StaticCatalogProjectionError(f"{context} 不是有效 JSON") from exc


def _string_tuple(value: object, *, context: str) -> tuple[str, ...]:
    parsed = _loads(value, context=context)
    if not isinstance(parsed, list):
        raise StaticCatalogProjectionError(f"{context} 必须是 JSON 数组")
    return tuple(str(item) for item in parsed if str(item).strip())


def _float_tuple(value: object, *, context: str) -> tuple[float, ...]:
    parsed = _loads(value, context=context)
    if not isinstance(parsed, list):
        raise StaticCatalogProjectionError(f"{context} 必须是 JSON 数组")
    return tuple(float(item) for item in parsed)


def _source(row: Mapping[str, Any], table: str, prefix: str = "source") -> CatalogSource:
    return CatalogSource(
        table_name=table,
        row_id=_optional_int(row.get(f"{prefix}_row_id")),
        row_key=_optional_text(row.get(f"{prefix}_row_key")),
        relative_path=_optional_text(row.get(f"{prefix}_relative_path")),
        content_sha256=_optional_text(row.get(f"{prefix}_content_sha256")),
        file_sha256=_optional_text(row.get(f"{prefix}_file_sha256")),
        payload_available=bool(row.get(f"{prefix}_payload_available", False)),
    )


def _flatten_structured(value: object, path: str = "$") -> tuple[StructuredEffectField, ...]:
    fields: list[StructuredEffectField] = []
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            fields.extend(_flatten_structured(value[key], f"{path}.{key}"))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            fields.extend(_flatten_structured(item, f"{path}[{index}]"))
    else:
        fields.append(StructuredEffectField(
            path=path,
            value_json=json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        ))
    return tuple(fields)


class StaticCatalogCharacterService:
    """Project normalized static character facts without consulting account state."""

    def __init__(self, queries: CharacterCatalogQueries) -> None:
        self._queries = queries
        self._passives = StaticCatalogCharacterPassiveService(queries)

    def dataset(self) -> CatalogDataset:
        row = self._queries.character_catalog_metadata()
        return CatalogDataset(
            dataset_id=str(row["dataset_id"]),
            game_version=_optional_text(row.get("game_version")),
            schema_version=int(row["schema_version"]),
            importer_version=int(row["importer_version"]),
            built_at_utc=str(row["built_at_utc"]),
        )

    def list_characters(
        self, *, query: str = "", limit: int = 50, offset: int = 0,
    ) -> CharacterPage:
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        normalized_query = str(query or "").strip()
        rows = self._queries.list_catalog_characters(
            query=normalized_query, limit=safe_limit, offset=safe_offset,
        )
        return CharacterPage(
            dataset=self.dataset(),
            query=normalized_query,
            offset=safe_offset,
            limit=safe_limit,
            total=self._queries.count_catalog_characters(normalized_query),
            items=tuple(self._summary(row) for row in rows),
        )

    def get_character_detail(self, character_id: int) -> CharacterDetail | None:
        raw_character_id = int(character_id)
        row = self._queries.get_catalog_character(raw_character_id)
        if row is None:
            return None
        growth_count = self._queries.count_catalog_growth_points(raw_character_id)
        progression = self._progression(raw_character_id)
        return CharacterDetail(
            dataset=self.dataset(),
            character=self._summary(row, growth_count=growth_count),
            name_text_table=_optional_text(row.get("name_text_table")),
            name_text_key=_optional_text(row.get("name_text_key")),
            annotation_source=_optional_text(row.get("annotation_source")),
            breakthroughs=self._breakthroughs(raw_character_id),
            likeability=self._likeability(raw_character_id),
            awakenings=self._awakenings(raw_character_id),
            skills=self._skills(raw_character_id),
            passives=self._passives.list_passives(row),
            cultivation=self._cultivation(raw_character_id),
            graduation=self._graduation(raw_character_id),
            equipment_plan=self._equipment_plan(raw_character_id),
            shape_bonus=self._shape_bonus(raw_character_id),
            recommended_weights=self._recommended_weights(raw_character_id),
            growth_count=growth_count,
            combat_link_count=self._queries.count_catalog_combat_links(raw_character_id),
            gaps=self._gaps(growth_count, progression),
            progression=progression,
        )

    def list_growth(
        self, character_id: int, *, limit: int = 40, offset: int = 0,
    ) -> GrowthPage:
        safe_limit = max(1, min(int(limit), 200))
        safe_offset = max(0, int(offset))
        raw_character_id = int(character_id)
        rows = self._queries.list_catalog_growth_points(
            raw_character_id, limit=safe_limit, offset=safe_offset,
        )
        return GrowthPage(
            character_id=raw_character_id,
            offset=safe_offset,
            limit=safe_limit,
            total=self._queries.count_catalog_growth_points(raw_character_id),
            items=tuple(self._growth_point(row) for row in rows),
        )

    def list_combat_links(
        self, character_id: int, *, limit: int = 100, offset: int = 0,
    ) -> CombatLinkPage:
        safe_limit = max(1, min(int(limit), 500))
        safe_offset = max(0, int(offset))
        raw_character_id = int(character_id)
        rows = self._queries.list_catalog_combat_links(
            raw_character_id, limit=safe_limit, offset=safe_offset,
        )
        return CombatLinkPage(
            character_id=raw_character_id,
            offset=safe_offset,
            limit=safe_limit,
            total=self._queries.count_catalog_combat_links(raw_character_id),
            items=tuple(self._combat_link(row) for row in rows),
        )

    def _summary(
        self, row: Mapping[str, Any], *, growth_count: int | None = None,
    ) -> CharacterSummary:
        return CharacterSummary(
            character_id=int(row["character_id"]),
            name_zh=str(row["name_zh"]),
            element_type=_optional_text(row.get("element_type")),
            element_label=_element_label(row.get("element_type")),
            group_type=_optional_text(row.get("group_type")),
            actor_path=_optional_text(row.get("actor_path")),
            mainland_show_time=_optional_text(row.get("mainland_show_time")),
            logical_character_key=_optional_text(row.get("logical_character_key")),
            canonical_character_id=_optional_int(row.get("canonical_character_id")),
            classification=_optional_text(row.get("classification")),
            growth_count=(
                int(row.get("growth_count") or 0)
                if growth_count is None else int(growth_count)
            ),
            skill_count=int(row.get("skill_count") or 0),
            awakening_count=int(row.get("awakening_count") or 0),
            has_graduation=bool(row.get("has_graduation", False)),
            source=_source(row, "character"),
        )

    @staticmethod
    def _growth_point(row: Mapping[str, Any]) -> GrowthPoint:
        breakthrough_source = None
        if row.get("breakthrough_modify_source_row_id") is not None:
            breakthrough_source = _source(
                row, "character_panel_growth.breakthrough", "breakthrough_source",
            )
        return GrowthPoint(
            level=int(row["level"]),
            breakthrough_stage=int(row["breakthrough_stage"]),
            state=str(row["state"]),
            hp_base=float(row["hp_base"]),
            atk_base=float(row["atk_base"]),
            def_base=float(row["def_base"]),
            player_pack_source=_source(
                row, "character_panel_growth.player_pack", "player_pack_source",
            ),
            level_curve_source=_source(
                row, "character_panel_growth.level_modify", "level_modify_source",
            ),
            breakthrough_source=breakthrough_source,
        )

    def _breakthroughs(self, character_id: int) -> tuple[BreakthroughStage, ...]:
        grouped: dict[int, dict[str, GrowthPoint]] = {}
        for row in self._queries.list_catalog_breakthrough_points(character_id):
            point = self._growth_point(row)
            grouped.setdefault(point.level, {})[point.state] = point
        result = []
        for level in sorted(grouped):
            before = grouped[level].get("breakthrough_before")
            after = grouped[level].get("breakthrough_after")
            if before is not None and after is not None:
                result.append(BreakthroughStage(
                    level=level, stage=after.breakthrough_stage, before=before, after=after,
                ))
        return tuple(result)

    def _progression(self, character_id: int) -> CharacterProgressionProfile | None:
        row = self._queries.get_catalog_character_progression(character_id)
        if row is None:
            return None
        return project_character_progression(row)

    def _likeability(self, character_id: int) -> LikeabilityBonus | None:
        row = self._queries.get_catalog_likeability(character_id)
        if row is None:
            return None
        return LikeabilityBonus(
            required_level=int(row["required_level"]),
            modify_data_id=str(row["modify_data_id"]),
            properties=tuple(
                LikeabilityProperty(
                    property_id=str(item["property_id"]),
                    display_name=str(item.get("display_name_zh") or item["property_id"]),
                    value=float(item["value"]),
                    modifier_operation=str(item["modifier_operation"]),
                    show_percent=bool(item.get("show_percent")),
                    source=_source(item, "character_likeability_bonus_property"),
                )
                for item in row.get("properties", ())
            ),
            role_source=_source(row, "character_likeability_bonus"),
            modifier_source=_source(
                row, "character_likeability_bonus.modifier", "modifier_source",
            ),
        )

    def _awakenings(self, character_id: int) -> tuple[AwakeningEffect, ...]:
        result = []
        for row in self._queries.list_catalog_awakenings(character_id):
            effect_id = str(row["effect_id"])
            structured = _loads(
                row["modify_data_json"], context=f"觉醒 {effect_id} modify_data_json",
            )
            result.append(AwakeningEffect(
                effect_id=effect_id,
                ordinal=int(row["ordinal"]),
                awaken_type=str(row["awaken_type"]),
                title_zh=_optional_text(row.get("title_zh")),
                description_zh=_optional_text(row.get("description_zh")),
                icon_path=_optional_text(row.get("icon_path")),
                structured_effects=_flatten_structured(structured),
                gameplay_effect_ids=_string_tuple(
                    row["gameplay_effect_ids_json"],
                    context=f"觉醒 {effect_id} gameplay_effect_ids_json",
                ),
                buff_definition_ids=tuple(
                    str(value) for value in row.get("buff_definition_ids", ())
                ),
                skill_level_bonuses=tuple(
                    SkillLevelBonus(
                        skill_id=str(item["skill_id"]),
                        level_delta=int(item["level_delta"]),
                    )
                    for item in row.get("skill_level_bonuses", ())
                ),
                source=_source(row, "character_awaken_effect"),
            ))
        return tuple(result)

    @staticmethod
    def _costs(value: object, *, context: str) -> tuple[CostItem, ...]:
        parsed = _loads(value, context=context)
        if not isinstance(parsed, list):
            raise StaticCatalogProjectionError(f"{context} 必须是 JSON 数组")
        result = []
        for item in parsed:
            if not isinstance(item, Mapping) or not str(item.get("ID") or "").strip():
                raise StaticCatalogProjectionError(f"{context} 包含无效材料项")
            result.append(CostItem(
                item_id=str(item["ID"]),
                quantity=float(item.get("Number") or 0),
                hidden_amount=bool(item.get("bHiddenAmount", False)),
            ))
        return tuple(result)

    def _skills(self, character_id: int) -> tuple[CharacterSkill, ...]:
        result = []
        for row in self._queries.list_catalog_skills(character_id):
            skill_id = str(row["skill_id"])
            result.append(CharacterSkill(
                skill_id=skill_id,
                name_zh=_optional_text(row.get("name_zh")),
                ability_type=str(row["ability_type"]),
                ability_index=int(row["ability_index"]),
                show_detail_info=bool(row["show_detail_info"]),
                gameplay_tag=_optional_text(row.get("gameplay_tag")),
                gameplay_effect_path=_optional_text(row.get("gameplay_effect_path")),
                gameplay_ability_path=_optional_text(row.get("gameplay_ability_path")),
                reapply_after_revive=bool(row["reapply_after_revive"]),
                icon_path=_optional_text(row.get("icon_path")),
                extended_icon_path=_optional_text(row.get("extended_icon_path")),
                levels=tuple(self._skill_level(skill_id, item) for item in row.get("levels", ())),
                descriptions=tuple(self._skill_description(item) for item in row.get("descriptions", ())),
                level_hints=tuple(self._skill_hint(skill_id, item) for item in row.get("level_hints", ())),
                damage_items=tuple(
                    SkillDamageItem(
                        damage_id=str(item["damage_id"]),
                        damage_type=str(item["damage_type"]),
                        atk_rates=_float_tuple(
                            item["atk_rate_base_json"],
                            context=f"技能 {skill_id} 伤害攻击倍率",
                        ),
                        def_rates=_float_tuple(
                            item["def_rate_base_json"],
                            context=f"技能 {skill_id} 伤害防御倍率",
                        ),
                        hp_rates=_float_tuple(
                            item["hp_rate_base_json"],
                            context=f"技能 {skill_id} 伤害生命倍率",
                        ),
                    )
                    for item in row.get("damage_items", ())
                ),
                ability_source=_source(row, "character_skill.ability", "ability_source"),
                effect_source=(
                    _source(row, "character_skill.effect", "effect_source")
                    if row.get("effect_source_row_id") is not None else None
                ),
            ))
        return tuple(result)

    def _skill_level(self, skill_id: str, row: Mapping[str, Any]) -> SkillLevel:
        return SkillLevel(
            level=int(row["level"]),
            required_breakthrough_stage=int(row["required_breakthrough_stage"]),
            required_awaken_level=int(row["required_awaken_level"]),
            costs=self._costs(
                row["cost_items_json"],
                context=f"技能 {skill_id} 等级 {row['level']} 消耗",
            ),
        )

    @staticmethod
    def _skill_description(row: Mapping[str, Any]) -> SkillDescription:
        return SkillDescription(
            ordinal=int(row["ordinal"]),
            description_type=_optional_text(row.get("description_type")),
            title_zh=_optional_text(row.get("title_zh")),
            description_zh=_optional_text(row.get("description_zh")),
            short_description_zh=_optional_text(row.get("short_description_zh")),
            unlock_id=_optional_text(row.get("unlock_id")),
            unlock_description_zh=_optional_text(row.get("unlock_description_zh")),
        )

    @staticmethod
    def _skill_hint(skill_id: str, row: Mapping[str, Any]) -> SkillLevelHint:
        return SkillLevelHint(
            ordinal=int(row["ordinal"]),
            name_id=_optional_text(row.get("name_id")),
            description_zh=_optional_text(row.get("description_zh")),
            value_description_zh=_optional_text(row.get("value_description_zh")),
            global_curve_id=_optional_text(row.get("global_curve_id")),
            source_type=_optional_text(row.get("source_type")),
            damage_effect_ids=_string_tuple(
                row["damage_effect_ids_json"],
                context=f"技能 {skill_id} 等级提示 damage_effect_ids_json",
            ),
            defense_effect_ids=_string_tuple(
                row["defense_effect_ids_json"],
                context=f"技能 {skill_id} 等级提示 defense_effect_ids_json",
            ),
            health_effect_ids=_string_tuple(
                row["health_effect_ids_json"],
                context=f"技能 {skill_id} 等级提示 health_effect_ids_json",
            ),
        )

    def _cultivation(self, character_id: int) -> CultivationGuide | None:
        row = self._queries.get_catalog_cultivation(character_id)
        if row is None:
            return None
        skills_by_stage: dict[int, list[tuple[str, str, int]]] = {}
        for item in row.get("stage_skills", ()):
            skills_by_stage.setdefault(int(item["stage_ordinal"]), []).append((
                str(item["sex_kind"]), str(item["ability_id"]), int(item["recommended_level"]),
            ))
        return CultivationGuide(
            s_score=float(row["s_score"]),
            a_score=float(row["a_score"]),
            icon_path=_optional_text(row.get("icon_path")),
            recommend_attribute_jump_id=_optional_text(row.get("recommend_attribute_jump_id")),
            fork_recommendations=tuple(
                (
                    str(item["fork_id"]),
                    str(item.get("fork_name_zh") or item["fork_id"]),
                    _optional_text(item.get("description_zh")),
                )
                for item in row.get("fork_recommendations", ())
            ),
            attribute_recommendations=tuple(
                (
                    str(item["property_id"]),
                    str(item.get("display_name_zh") or item["property_id"]),
                )
                for item in row.get("attribute_recommendations", ())
            ),
            stages=tuple(
                CultivationStage(
                    ordinal=int(item["stage_ordinal"]),
                    character_level=int(item["character_level"]),
                    fork_level=int(item["fork_level"]),
                    core_item_id=str(item["core_item_id"]),
                    core_level=int(item["core_level"]),
                    equipment_level=int(item["equipment_level"]),
                    recommended_skills=tuple(
                        skills_by_stage.get(int(item["stage_ordinal"]), ())
                    ),
                )
                for item in row.get("stages", ())
            ),
            source=_source(row, "character_cultivation_guide"),
        )

    def _graduation(self, character_id: int) -> GraduationTemplate | None:
        row = self._queries.get_catalog_graduation(character_id)
        if row is None:
            return None
        equipment = _loads(
            row["equipment_json"], context=f"角色 {character_id} 毕业装备模板",
        )
        if not isinstance(equipment, list):
            raise StaticCatalogProjectionError("毕业装备模板必须是 JSON 数组")
        core_stats: list[BuildProperty] = []
        drive_stats: list[BuildProperty] = []
        for item in equipment:
            if not isinstance(item, Mapping):
                continue
            target = core_stats if item.get("kind") == "core" else drive_stats
            stat_key = "main_stats" if item.get("kind") == "core" else "sub_stats"
            for stat in item.get(stat_key, ()):
                if not isinstance(stat, Mapping) or not stat.get("property_id"):
                    continue
                target.append(BuildProperty(
                    property_id=str(stat["property_id"]),
                    display_name=None,
                    value=float(stat["value"]) if stat.get("value") is not None else None,
                    show_percent=bool(stat.get("percent")),
                ))
        return GraduationTemplate(
            source_kind=str(row["source_kind"]),
            fork_id=_optional_text(row.get("fork_id")),
            fork_name_zh=_optional_text(row.get("fork_name_zh")),
            fork_level=_optional_int(row.get("fork_level")),
            fork_refinement_level=_optional_int(row.get("fork_refinement_level")),
            core_suit_id=_optional_text(row.get("core_suit_id")),
            core_suit_name_zh=_optional_text(row.get("core_suit_name_zh")),
            core_main_property_id=_optional_text(row.get("core_main_property_id")),
            core_main_property_name_zh=_optional_text(row.get("core_main_property_name_zh")),
            drive_area=int(row["drive_area"]),
            extra_shape_count=int(row["extra_shape_count"]),
            benchmark_damage=float(row["benchmark_damage"]),
            generated_at_utc=str(row["generated_at_utc"]),
            fork_paths=tuple(
                path
                for key in ("fork_icon_path", "fork_card_path", "fork_painting_path")
                if (path := _optional_text(row.get(key))) is not None
            ),
            fork_source=(
                _source(row, "fork_item", "fork_source")
                if row.get("fork_source_row_id") is not None else None
            ),
            core_main_stats=tuple(core_stats),
            drive_template_stats=tuple(drive_stats),
        )

    def _equipment_plan(self, character_id: int) -> CharacterEquipmentPlan | None:
        row = self._queries.get_catalog_equipment_plan(character_id)
        if row is None:
            return None
        modules = []
        board: dict[tuple[int, int], int] = {}
        anchors_by_item: dict[str, list[tuple[int, int]]] = {}
        for anchor in row.get("anchors", ()):
            anchors_by_item.setdefault(str(anchor["anchor_item_id"]), []).append((
                int(anchor["row"]),
                int(anchor["column"]),
            ))
        module_counts = Counter(
            str(item["item_id"]) for item in row.get("modules", ())
        )
        anchor_counts = Counter({
            item_id: len(anchors) for item_id, anchors in anchors_by_item.items()
        })
        if module_counts != anchor_counts:
            raise StaticCatalogProjectionError(
                f"角色 {character_id} 图纸模块与锚点数量不一致"
            )
        anchor_offsets: dict[str, int] = {}
        for item in row.get("modules", ()):
            item_id = str(item["item_id"])
            offset = anchor_offsets.get(item_id, 0)
            item_anchors = anchors_by_item.get(item_id, ())
            anchor = item_anchors[offset] if offset < len(item_anchors) else None
            anchor_offsets[item_id] = offset + 1
            anchor_row = anchor[0] if anchor is not None else None
            anchor_column = anchor[1] if anchor is not None else None
            occupied = ()
            if anchor_row is not None and anchor_column is not None:
                occupied = tuple(
                    (anchor_row + int(cell["x"]), anchor_column + int(cell["y"]))
                    for cell in item.get("shape_cells", ())
                )
                if any(
                    not 1 <= row_index <= 5 or not 1 <= column_index <= 5
                    for row_index, column_index in occupied
                ):
                    raise StaticCatalogProjectionError(
                        f"角色 {character_id} 图纸模块越出 5×5 底盘"
                    )
            ordinal = int(item["ordinal"])
            for cell in occupied:
                if cell in board:
                    raise StaticCatalogProjectionError(
                        f"角色 {character_id} 图纸模块发生重叠"
                    )
                board[cell] = ordinal
            modules.append(EquipmentPlanModule(
                ordinal=ordinal,
                item_id=item_id,
                display_name=_optional_text(item.get("name_zh")),
                shape_id=_optional_text(item.get("geometry_id")),
                grid_count=int(item.get("grid_count") or len(occupied)),
                anchor_row=anchor_row,
                anchor_column=anchor_column,
                occupied_cells=occupied,
            ))
        if len(board) != 20:
            raise StaticCatalogProjectionError(
                f"角色 {character_id} 正式图纸应覆盖 20 格，实际 {len(board)} 格"
            )
        cells = tuple(
            (row_index, column_index, board.get((row_index, column_index)))
            for row_index in range(1, 6)
            for column_index in range(1, 6)
        )
        return CharacterEquipmentPlan(
            core_item_id=str(row["core_item_id"]),
            core_level=int(row["core_level"]),
            module_level=int(row["module_level"]),
            cells=cells,
            modules=tuple(modules),
            core_attributes=self._build_properties(row.get("core_attributes", ())),
            recommended_attributes=self._build_properties(
                row.get("recommended_attributes", ()),
            ),
        )

    def _shape_bonus(self, character_id: int) -> CharacterShapeBonus | None:
        row = self._queries.get_catalog_character_shape_bonus(character_id)
        if row is None:
            return None
        return CharacterShapeBonus(
            shape_label=str(row["shape_label"]),
            shape_grid_count=int(row["shape_grid_count"]),
            properties=self._build_properties(row.get("properties", ())),
        )

    def _recommended_weights(
        self, character_id: int,
    ) -> CharacterWeightRecommendation | None:
        row = self._queries.get_catalog_character_weights(character_id)
        if row is None:
            return None
        return CharacterWeightRecommendation(properties=tuple(
            (
                self._build_property(item),
                float(item["weight"]),
                float(item["main_weight"]),
            )
            for item in row.get("properties", ())
        ))

    @classmethod
    def _build_properties(
        cls, rows: Iterable[Mapping[str, Any]],
    ) -> tuple[BuildProperty, ...]:
        return tuple(cls._build_property(row) for row in rows)

    @staticmethod
    def _build_property(row: Mapping[str, Any]) -> BuildProperty:
        return BuildProperty(
            property_id=str(row["property_id"]),
            display_name=_optional_text(row.get("display_name_zh")),
            value=(
                float(row["display_value"])
                if row.get("display_value") is not None else None
            ),
            show_percent=bool(row.get("show_percent")),
        )

    @staticmethod
    def _combat_link(row: Mapping[str, Any]) -> CombatLink:
        binding_kind = str(row["binding_kind"])
        return CombatLink(
            relationship_kind=(
                "character_owned_buff" if binding_kind == "owned_buff"
                else "ability_effect"
            ),
            binding_kind=binding_kind,
            input_id=_optional_text(row.get("input_id")),
            ability_id=_optional_text(row.get("ability_id")),
            ability_name_zh=_optional_text(row.get("ability_name_zh")),
            ability_asset_path=_optional_text(row.get("ability_asset_path")),
            event_tag=_optional_text(row.get("event_tag")),
            gameplay_effect_id=_optional_text(row.get("effect_id")),
            gameplay_effect_index=_optional_int(row.get("gameplay_effect_index")),
            effect_asset_path=_optional_text(row.get("effect_asset_path")),
            gameplay_effect_class_path=_optional_text(row.get("gameplay_effect_class_path")),
            target_type_asset_path=_optional_text(row.get("target_type_asset_path")),
            buff_definition_id=_optional_text(row.get("buff_definition_id")),
            buff_definition_kind=_optional_text(row.get("buff_definition_kind")),
            duration_policy=_optional_text(row.get("duration_policy")),
            stacking_type=_optional_text(row.get("stacking_type")),
            stack_limit_count=_optional_int(row.get("stack_limit_count")),
            source=CatalogSource(
                table_name=(
                    "buff_definition" if binding_kind == "owned_buff"
                    else "combat_ability_effect_binding"
                ),
                relative_path=_optional_text(row.get("source_relative_path")),
            ),
        )

    @staticmethod
    def _gaps(
        growth_count: int,
        progression: CharacterProgressionProfile | None,
    ) -> tuple[CatalogGap, ...]:
        gaps = []
        if progression is None:
            gaps.append(CatalogGap(
                field_key="character_progression_profile",
                label="人物升级与突破消耗",
                status="unavailable",
                reason="该角色没有独立的正式人物养成包",
            ))
        if growth_count == 0:
            gaps.append(CatalogGap(
                field_key="character_panel_growth",
                label="1–80 级人物面板曲线",
                status="unavailable",
                reason="该角色没有 character_panel_growth 行；常见于战斗变身或未完整导入角色",
            ))
        return tuple(gaps)
