# 导入培养指南、技能文本、怪物手册和装备效果来源。
"""Static combat-catalog imports kept separate from legacy combat facts."""

from __future__ import annotations

import json
from typing import Any

from tools.game_data.static_database_build_support import *


def _localized_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    text = (
        value.get("LocalizedString")
        or value.get("SourceString")
        or value.get("CultureInvariantString")
    )
    return text if isinstance(text, str) and text else None


def _plain_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


class CatalogImportMixin:
    def _import_combat_catalogs(self) -> None:
        self._import_gameplay_abilities()
        self._import_gameplay_effect_catalog()
        self._import_monster_catalog()
        self._import_equipment_effect_sources()
        self._import_cultivation_guides()
        self._import_combat_effect_definitions()
        self._validate_fork_refinement_links()

    def _import_gameplay_abilities(self) -> None:
        for ability_id in sorted(self.rows["gameplay_ability_tips"]):
            row = self.rows["gameplay_ability_tips"][ability_id]
            name, name_table, name_key = text_parts(row.get("Name"))
            self.connection.execute(
                "INSERT INTO gameplay_ability_catalog VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    ability_id,
                    name,
                    name_table,
                    name_key,
                    asset_path(row.get("icon")),
                    asset_path(row.get("ExtendIcon")),
                    asset_path(row.get("GameplayAbility")),
                    bool_int(row.get("bStolenAbility")),
                    self.source_row_id("gameplay_ability_tips", ability_id),
                ),
            )
            for ordinal, description in enumerate(
                _plain_list(row.get("AbilityDescription"))
            ):
                if not isinstance(description, dict):
                    raise StaticDatabaseError(
                        f"技能说明不是对象：{ability_id}/{ordinal}"
                    )
                description_zh, description_table, description_key = text_parts(
                    description.get("Description")
                )
                self.connection.execute(
                    """
                    INSERT INTO gameplay_ability_description VALUES (
                        ?,?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        ability_id,
                        ordinal,
                        enum_tail(description.get("AbilityDesType")),
                        _localized_text(description.get("DescriptionTitle")),
                        description_zh,
                        description_table,
                        description_key,
                        _localized_text(description.get("ShortDescription")),
                        optional_text(description.get("UnLockID")),
                        _localized_text(description.get("UnlockDesc")),
                        canonical_json(
                            _plain_list(description.get("AbilityDesReplaceValue"))
                        ),
                    ),
                )
            for ordinal, hint in enumerate(_plain_list(row.get("GALevelUpArray"))):
                if not isinstance(hint, dict):
                    raise StaticDatabaseError(
                        f"技能升级说明不是对象：{ability_id}/{ordinal}"
                    )
                source = hint.get("LevelUpData")
                source = source if isinstance(source, dict) else {}
                self.connection.execute(
                    """
                    INSERT INTO gameplay_ability_level_hint VALUES (
                        ?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        ability_id,
                        ordinal,
                        optional_text(hint.get("Name")),
                        _localized_text(hint.get("Desc")),
                        _localized_text(hint.get("DescValue")),
                        optional_text(hint.get("CLGlobalCommonDataCurveID")),
                        enum_tail(source.get("LevelUpGESourceType")),
                        canonical_json(_plain_list(source.get("DamageGEList"))),
                        canonical_json(_plain_list(source.get("DefGEList"))),
                        canonical_json(_plain_list(source.get("HPGEList"))),
                    ),
                )

    def _import_gameplay_effect_catalog(self) -> None:
        for effect_id in sorted(self.rows["gameplay_effect_mapping"]):
            row = self.rows["gameplay_effect_mapping"][effect_id]
            unique_index = row.get("UniqueIndex")
            if isinstance(unique_index, bool) or not isinstance(unique_index, int):
                raise StaticDatabaseError(
                    f"GameplayEffect 缺少整数索引：{effect_id}"
                )
            self.connection.execute(
                "INSERT INTO gameplay_effect_catalog VALUES (?,?,?,?)",
                (
                    unique_index,
                    effect_id,
                    asset_path(row.get("GameplayEffectClass")),
                    self.source_row_id("gameplay_effect_mapping", effect_id),
                ),
            )

    def _import_monster_catalog(self) -> None:
        scalar_aliases = {
            "monster_tag": "MonsterTag",
            "vision_id": "VisionID",
            "world_boss_id": "WorldBossID",
            "clone_id": "CloneID",
            "clone_enter_id": "CloneEnterID",
        }
        for monster_id in sorted(self.rows["monster_manual"]):
            row = self.rows["monster_manual"][monster_id]
            name = _localized_text(row.get("MonsterName"))
            if name is None:
                raise StaticDatabaseError(f"怪物手册缺少名称：{monster_id}")
            self.connection.execute(
                "INSERT INTO monster_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    monster_id,
                    int(row.get("Sort") or 0),
                    name,
                    enum_tail(row.get("EnemyType")),
                    asset_path(row.get("EnemyImage")),
                    asset_path(row.get("EnemyWorldImage")),
                    _localized_text(row.get("EnemyPlace")),
                    _localized_text(row.get("DiscoveredDesc")),
                    _localized_text(row.get("UndiscoveredDesc")),
                    optional_text(row.get("DropID")),
                    optional_int(row.get("StaminaNum")),
                    enum_tail(row.get("TraceType")),
                    optional_text(row.get("MapIconID")),
                    optional_text(row.get("QuestId")),
                    self.source_row_id("monster_manual", monster_id),
                ),
            )
            aliases: list[tuple[str, str]] = []
            for alias_kind, field in scalar_aliases.items():
                value = optional_text(row.get(field))
                if value is not None:
                    aliases.append((alias_kind, value))
            for value in _plain_list(row.get("ArraySpawnerID")):
                normalized = optional_text(value)
                if normalized is not None:
                    aliases.append(("spawner_id", normalized))
            counters: dict[str, int] = {}
            for alias_kind, alias_value in aliases:
                ordinal = counters.get(alias_kind, 0)
                counters[alias_kind] = ordinal + 1
                self.connection.execute(
                    "INSERT INTO monster_identifier_alias VALUES (?,?,?,?)",
                    (monster_id, alias_kind, ordinal, alias_value),
                )

    def _import_equipment_effect_sources(self) -> None:
        for pack_id in sorted(self.rows["equipment_modify"]):
            row = self.rows["equipment_modify"][pack_id]
            self.connection.execute(
                "INSERT INTO equipment_modify_pack VALUES (?,?,?)",
                (
                    pack_id,
                    canonical_json(_plain_list(row.get("ConditionArray"))),
                    self.source_row_id("equipment_modify", pack_id),
                ),
            )
            for ordinal, modifier in enumerate(_plain_list(row.get("ModifyData"))):
                if not isinstance(modifier, dict):
                    raise StaticDatabaseError(
                        f"装备 ModifyData 不是对象：{pack_id}/{ordinal}"
                    )
                self.connection.execute(
                    "INSERT INTO equipment_modify_value VALUES (?,?,?,?,?,?)",
                    (
                        pack_id,
                        ordinal,
                        str(modifier.get("PropName") or ""),
                        float(modifier.get("PropValue") or 0.0),
                        enum_tail(modifier.get("ModifierOp")) or "",
                        modifier.get("SortKey"),
                    ),
                )
        for curve_id in sorted(self.rows["equipment_buff_curves"]):
            row = self.rows["equipment_buff_curves"][curve_id]
            self.connection.execute(
                "INSERT INTO equipment_buff_curve VALUES (?,?,?,?,?,?)",
                (
                    curve_id,
                    enum_tail(row.get("InterpMode")),
                    row.get("DefaultValue"),
                    enum_tail(row.get("PreInfinityExtrap")),
                    enum_tail(row.get("PostInfinityExtrap")),
                    self.source_row_id("equipment_buff_curves", curve_id),
                ),
            )
            for ordinal, point in enumerate(_plain_list(row.get("Keys"))):
                if not isinstance(point, dict):
                    raise StaticDatabaseError(
                        f"装备 Buff 曲线点不是对象：{curve_id}/{ordinal}"
                    )
                self.connection.execute(
                    "INSERT INTO equipment_buff_curve_point VALUES (?,?,?,?)",
                    (
                        curve_id,
                        ordinal,
                        float(point["Time"]),
                        float(point["Value"]),
                    ),
                )

    def _import_cultivation_guides(self) -> None:
        for raw_character_id in sorted(
            self.rows["cultivation_guides"], key=lambda value: int(value)
        ):
            character_id = int(raw_character_id)
            row = self.rows["cultivation_guides"][raw_character_id]
            self.connection.execute(
                "INSERT INTO character_cultivation_guide VALUES (?,?,?,?,?,?,?,?)",
                (
                    character_id,
                    bool_int(row.get("bDisplayText")),
                    float(row.get("SScore") or 0.0),
                    float(row.get("AScore") or 0.0),
                    asset_path(row.get("CharacterIcon")),
                    optional_text(row.get("RecommendAttrJumpID")),
                    bool_int(row.get("RoleSexChange")),
                    self.source_row_id("cultivation_guides", raw_character_id),
                ),
            )
            for ordinal, recommendation in enumerate(
                _plain_list(row.get("RecommendForkList"))
            ):
                if not isinstance(recommendation, dict):
                    raise StaticDatabaseError(
                        f"推荐弧盘不是对象：{character_id}/{ordinal}"
                    )
                self.connection.execute(
                    """
                    INSERT INTO character_cultivation_fork_recommendation
                    VALUES (?,?,?,?,?)
                    """,
                    (
                        character_id,
                        ordinal,
                        str(recommendation.get("ForkItemId") or ""),
                        _localized_text(recommendation.get("Desc")),
                        optional_text(recommendation.get("SourceID")),
                    ),
                )
            for ordinal, property_id in enumerate(
                _plain_list(row.get("RecommendAttrList"))
            ):
                self.connection.execute(
                    """
                    INSERT INTO character_cultivation_attribute_recommendation
                    VALUES (?,?,?)
                    """,
                    (character_id, ordinal, str(property_id)),
                )
            for stage_ordinal, stage in enumerate(
                _plain_list(row.get("WorldLevelRecommendList"))
            ):
                if not isinstance(stage, dict):
                    raise StaticDatabaseError(
                        f"培养阶段不是对象：{character_id}/{stage_ordinal}"
                    )
                self.connection.execute(
                    "INSERT INTO character_cultivation_stage VALUES (?,?,?,?,?,?,?)",
                    (
                        character_id,
                        stage_ordinal,
                        int(stage.get("CharacterRecommendLevel") or 0),
                        int(stage.get("ForkRecommendLevel") or 0),
                        str(stage.get("CoreID") or ""),
                        int(stage.get("CoreLvl") or 0),
                        int(stage.get("EquipLvl") or 0),
                    ),
                )
                self._import_stage_skills(
                    character_id,
                    stage_ordinal,
                    "default",
                    stage.get("SkillRecommendGradeList"),
                )
                self._import_stage_skills(
                    character_id,
                    stage_ordinal,
                    "male",
                    stage.get("MaleSkillRecommendGradeList"),
                )

    def _import_stage_skills(
        self,
        character_id: int,
        stage_ordinal: int,
        sex_kind: str,
        skills: Any,
    ) -> None:
        for ordinal, skill in enumerate(_plain_list(skills)):
            if not isinstance(skill, dict):
                raise StaticDatabaseError(
                    f"培养技能不是对象：{character_id}/{stage_ordinal}/{ordinal}"
                )
            self.connection.execute(
                "INSERT INTO character_cultivation_stage_skill VALUES (?,?,?,?,?,?)",
                (
                    character_id,
                    stage_ordinal,
                    sex_kind,
                    ordinal,
                    str(skill.get("AbilityId") or ""),
                    int(skill.get("RecommendLevel") or 0),
                ),
            )

    def _import_combat_effect_definitions(self) -> None:
        self._import_suit_effect_definitions()
        self._import_fork_effect_definitions()
        self._import_awaken_effect_definitions()

    def _import_suit_effect_definitions(self) -> None:
        rows = self.connection.execute(
            """
            SELECT suit_id, required_count, modify_pack_id, buff_object_path,
                   description_zh, source_row_id
            FROM equipment_suit_effect
            """
        )
        for row in rows:
            suit_id, required_count, modify_pack_id, buff_path, description, source = row
            effect_kind = "modify_pack" if modify_pack_id else "buff_object"
            self.connection.execute(
                "INSERT INTO combat_effect_definition VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"equipment_suit:{suit_id}:{required_count}",
                    "equipment_suit",
                    str(suit_id),
                    effect_kind,
                    "equipped_set",
                    description,
                    canonical_json(
                        {
                            "required_count": int(required_count),
                            "modify_pack_id": modify_pack_id,
                            "buff_object_path": buff_path,
                        }
                    ),
                    1,
                    int(source),
                ),
            )

    def _import_fork_effect_definitions(self) -> None:
        rows = self.connection.execute(
            """
            SELECT star_pack_id, star_level, description_zh, source_row_id
            FROM fork_star_level
            ORDER BY star_pack_id, star_level
            """
        )
        for star_pack_id, star_level, description, source in rows:
            parameters = [
                {
                    "ordinal": int(item[0]),
                    "name_id": str(item[1]),
                    "is_percent": bool(item[2]),
                    "value": item[3],
                }
                for item in self.connection.execute(
                    """
                    SELECT p.ordinal, p.name_id, p.is_percent, v.value
                    FROM fork_star_parameter AS p
                    LEFT JOIN fork_refinement_parameter_value AS v
                      ON v.name_id = p.name_id
                     AND v.refinement_level = p.star_level
                    WHERE p.star_pack_id = ? AND p.star_level = ?
                    ORDER BY p.ordinal
                    """,
                    (star_pack_id, star_level),
                )
            ]
            self.connection.execute(
                "INSERT INTO combat_effect_definition VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"fork_star:{star_pack_id}:{star_level}",
                    "fork_star",
                    str(star_pack_id),
                    "description_parameters",
                    "equipped_fork",
                    description,
                    canonical_json(parameters),
                    1,
                    int(source),
                ),
            )

    def _import_awaken_effect_definitions(self) -> None:
        rows = self.connection.execute(
            """
            SELECT character_id, effect_id, awaken_type, description_zh,
                   modify_data_json, gameplay_effect_ids_json, source_row_id
            FROM character_awaken_effect
            ORDER BY character_id, ordinal
            """
        )
        for row in rows:
            character_id, effect_id, awaken_type, description = row[:4]
            self.connection.execute(
                "INSERT INTO combat_effect_definition VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    f"character_awaken:{character_id}:{effect_id}",
                    "character_awaken",
                    f"{character_id}:{effect_id}",
                    str(awaken_type),
                    "awakening_unlocked",
                    description,
                    canonical_json(
                        {
                            "modify_data": json.loads(str(row[4]) or "[]"),
                            "gameplay_effect_ids": json.loads(str(row[5]) or "[]"),
                        }
                    ),
                    1,
                    int(row[6]),
                ),
            )

    def _validate_fork_refinement_links(self) -> None:
        invalid = list(
            self.connection.execute(
                """
                SELECT f.fork_id, f.star_pack_id, f.max_star, COUNT(s.star_level)
                FROM fork_item AS f
                LEFT JOIN fork_star_level AS s
                  ON s.star_pack_id = f.star_pack_id
                WHERE COALESCE(f.max_star, 0) > 0
                GROUP BY f.fork_id, f.star_pack_id, f.max_star
                HAVING COUNT(s.star_level) <> f.max_star
                ORDER BY f.fork_id
                """
            )
        )
        if invalid:
            raise StaticDatabaseError(f"弧盘精炼定义关联不完整：{invalid[:10]}")
