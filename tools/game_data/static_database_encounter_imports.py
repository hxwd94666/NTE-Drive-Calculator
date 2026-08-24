# 导入争锋赏宴目标、加成与魔女赐福静态目录。
"""Encounter-selection static catalog importer."""

from __future__ import annotations

import re

from tools.game_data.static_database_build_support import *


_DIVINATION_PROPERTIES = {
    "Divination_AtkUp": ("攻击力提升15%", "AtkUp", True),
    "Divination_CritBase": ("暴击率提升10%", "CritBase", True),
    "Divination_CritDamageBase": ("暴击伤害提升20%", "CritDamageBase", True),
    "Divination_DamageUpGeneralBase": (
        "通用伤害提升15%", "DamageUpGeneralBase", True,
    ),
    "Divination_GetEfficiencyBase": ("蓄质速率提升20%", "GetEfficiencyBase", True),
    "Divination_UnbalIntensityBase": ("倾陷强度增加60", "UnbalIntensityBase", False),
    "Divination_MagBase": ("原质驱力增加60", "MagBase", False),
}

_AUDITED_OUTER_REALM_BUFF_COMPONENTS = {
    "Abyss_8": (
        (
            "corruption_damage_stack",
            "CritDamageBase",
            "Buff_Abyss_Phase_008_CritDmg",
            "Buff_Abyss_Phase_008_CD",
            1.0,
            8,
        ),
        (
            "while_target_toppled",
            "DamageUpGeneralBase",
            "Buff_Abyss_Phase_008_Up",
            None,
            None,
            1,
        ),
    ),
    "Abyss_9": (
        (
            "whole_battle",
            "DamageUpNatureBase",
            "Buff_Abyss_Phase_009_Nature",
            None,
            None,
            1,
        ),
        (
            "whole_battle",
            "DamageUpIncantationBase",
            "Buff_Abyss_Phase_009_Incantation",
            None,
            None,
            1,
        ),
    ),
}


def _boss_id(preview_path: str) -> str:
    match = re.search(r"/Monster/([^/]+)/", preview_path, re.IGNORECASE)
    if match is None:
        raise StaticDatabaseError(f"争锋赏宴预览模型无法解析怪物 ID：{preview_path}")
    return f"{match.group(1)}_BP_DiyBoss"


def _option_effect(option_id: str, buff_path: str | None) -> tuple[str, str | None]:
    normalized = (buff_path or option_id).casefold()
    if "timeop" in option_id.casefold():
        return "time_limit", None
    if "_hp_" in normalized or option_id.casefold().startswith("lifeop"):
        return "health_up", None
    if "_atk_" in normalized or option_id.casefold().startswith("attack"):
        return "attack_up", None
    elements = {
        "resistcosmos": "cosmos",
        "resistnature": "nature",
        "resistincantation": "incantation",
        "resistchaos": "chaos",
        "resistphyche": "psyche",
        "resistlakshana": "lakshana",
    }
    for marker, damage_type in elements.items():
        if marker in normalized:
            return "resistance_up", damage_type
    return "unknown", None


def _mainland_timestamp(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    mainland = value.get("MainlandTime")
    if not isinstance(mainland, dict):
        return None
    year = int(mainland.get("Year") or 0)
    if year <= 1:
        return None
    return (
        f"{year:04d}-{int(mainland.get('Month') or 1):02d}-"
        f"{int(mainland.get('Day') or 1):02d}T"
        f"{int(mainland.get('Hour') or 0):02d}:"
        f"{int(mainland.get('minute') or 0):02d}:"
        f"{int(mainland.get('Second') or 0):02d}"
    )


class EncounterImportMixin:
    def _import_encounter_catalogs(self) -> None:
        self._import_outer_realm_rotations()
        self._import_outer_realm_buffs()
        options = self.rows["feast_options"]
        for option_id, row in sorted(options.items()):
            node = row.get("OptionNode") or {}
            buffs = node.get("BossBuffs") or ()
            buff_path = asset_path(buffs[0]) if buffs else None
            effect_kind, damage_type = _option_effect(option_id, buff_path)
            add_percent = node.get("AddPercent")
            self.connection.execute(
                "INSERT INTO feast_option VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    option_id,
                    enum_tail(row.get("OptionType")) or "unknown",
                    effect_kind,
                    damage_type,
                    (
                        float(add_percent) / 100.0
                        if isinstance(add_percent, (int, float))
                        else None
                    ),
                    optional_int(node.get("LimitSeconds")),
                    int(node.get("Score") or 0),
                    buff_path,
                    self.source_row_id("feast_options", option_id),
                ),
            )

        for stage_id, row in sorted(self.rows["feast_stages"].items()):
            name, _, _ = text_parts(row.get("DescName"))
            if not name:
                raise StaticDatabaseError(f"争锋赏宴关卡缺少名称：{stage_id}")
            boss_id = _boss_id(str(asset_path(row.get("PreviewActorClass")) or ""))
            resolved = self.connection.execute(
                """
                SELECT monster_id FROM monster_instance_profile
                WHERE static_table = 'monster_static_big_world'
                  AND lower(monster_id) = lower(?)
                """,
                (boss_id,),
            ).fetchone()
            if resolved is None:
                raise StaticDatabaseError(f"争锋赏宴怪物未进入实例目录：{boss_id}")
            boss_id = str(resolved[0])
            source_row_id = self.source_row_id("feast_stages", stage_id)
            self.connection.execute(
                "INSERT INTO feast_stage VALUES (?,?,?,?,?)",
                (
                    stage_id,
                    name,
                    boss_id,
                    bool_int(row.get("bSpecialHighDifficulty")),
                    source_row_id,
                ),
            )
            displays = {
                int(item["Key"]): item["Value"]
                for item in row.get("DifficultyDisplayData") or ()
            }
            for difficulty in row.get("DifficultyMap") or ():
                difficulty_id = int(difficulty["Key"])
                value = difficulty["Value"]
                description, _, _ = text_parts(value.get("Desc"))
                display = displays.get(difficulty_id, {})
                boss_name, _, _ = text_parts(display.get("Desc"))
                variant = self.connection.execute(
                    """
                    SELECT threshold_level, profile_set, pack_id
                    FROM monster_instance_profile_variant
                    WHERE static_table = 'monster_static_big_world'
                      AND monster_id = ? AND variant_kind = 'clone_level'
                    ORDER BY threshold_level LIMIT 1 OFFSET ?
                    """,
                    (boss_id, difficulty_id - 1),
                ).fetchone()
                if variant is None:
                    raise StaticDatabaseError(
                        f"争锋赏宴难度缺少怪物属性包：{stage_id}/{difficulty_id}"
                    )
                self.connection.execute(
                    "INSERT INTO feast_stage_difficulty VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        stage_id,
                        difficulty_id,
                        description or str(difficulty_id),
                        boss_name or boss_id,
                        int(value.get("BaseScore") or 0),
                        float(value.get("ScoreRate") or 0.0),
                        int(variant[0]),
                        str(variant[1]),
                        str(variant[2]),
                        asset_path(display.get("BossIconMini")),
                    ),
                )
            for category_ordinal, category in enumerate(row.get("OptionChoices") or ()):
                category_name, _, _ = text_parts(category.get("CategoryDesc"))
                for option_ordinal, option_id in enumerate(category.get("Options") or ()):
                    if option_id not in options:
                        raise StaticDatabaseError(
                            f"争锋赏宴选项不存在：{stage_id}/{option_id}"
                        )
                    self.connection.execute(
                        "INSERT INTO feast_stage_option VALUES (?,?,?,?,?)",
                        (
                            stage_id,
                            category_ordinal,
                            option_ordinal,
                            category_name or f"选项 {category_ordinal + 1}",
                            option_id,
                        ),
                    )

        texts = self.rows["divination_text"]
        for curve_id, (name, property_id, is_percent) in _DIVINATION_PROPERTIES.items():
            curve = self.rows["divination_curves"].get(curve_id) or {}
            keys = curve.get("Keys") or ()
            if len(keys) != 1 or not isinstance(keys[0].get("Value"), (int, float)):
                raise StaticDatabaseError(f"魔女赐福曲线不是单值：{curve_id}")
            text_key = f"Buff_{curve_id}"
            description = str(texts.get(text_key) or "")
            self.connection.execute(
                "INSERT INTO divination_buff VALUES (?,?,?,?,?,?,?)",
                (
                    text_key,
                    name,
                    description,
                    property_id,
                    float(keys[0]["Value"]),
                    bool_int(is_percent),
                    self.source_row_id("divination_curves", curve_id),
                ),
            )
        self._import_official_activity_catalog()

    def _import_outer_realm_buffs(self) -> None:
        curves = self.rows["abyss_buff_curves"]

        def curve_value(curve_id: str) -> float:
            row = curves.get(curve_id) or {}
            keys = row.get("Keys") or ()
            if len(keys) != 1 or not isinstance(keys[0].get("Value"), (int, float)):
                raise StaticDatabaseError(f"轨外赛季 Buff 曲线不是单值：{curve_id}")
            return float(keys[0]["Value"])

        active_configs = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT level_config_id FROM outer_realm_rotation "
                "WHERE inference_ordinal IS NOT NULL"
            )
        }
        for config_id in sorted(active_configs):
            components = _AUDITED_OUTER_REALM_BUFF_COMPONENTS.get(config_id)
            if components is None:
                raise StaticDatabaseError(f"当前轨外配置尚未审计赛季 Buff：{config_id}")
            season = self.rows["abyss_seasons"].get(config_id) or {}
            buff_id = optional_text(season.get("BuffID"))
            buff = self.rows["abyss_buff_configs"].get(buff_id or "") or {}
            season_name, _, _ = text_parts(season.get("SeasonName"))
            buff_name, _, _ = text_parts(buff.get("BuffName"))
            description, _, _ = text_parts(buff.get("BuffDesc"))
            buff_entries = buff.get("CloneBuffArray") or ()
            gameplay_effect_path = (
                asset_path(buff_entries[0].get("BuffGE")) if buff_entries else None
            )
            if not all((buff_id, season_name, buff_name, description, gameplay_effect_path)):
                raise StaticDatabaseError(f"轨外赛季 Buff 元数据不完整：{config_id}")
            self.connection.execute(
                "INSERT INTO outer_realm_season_buff VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    config_id,
                    season_name,
                    buff_id,
                    buff_name,
                    description,
                    gameplay_effect_path,
                    bool_int(buff_entries[0].get("bAddToCharacter")),
                    self.source_row_id("abyss_seasons", config_id),
                    self.source_row_id("abyss_buff_configs", buff_id),
                ),
            )
            for ordinal, component in enumerate(components):
                trigger_kind, property_id, curve_id, duration_curve, cooldown, limit = component
                self.connection.execute(
                    "INSERT INTO outer_realm_season_buff_component "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        config_id,
                        ordinal,
                        trigger_kind,
                        property_id,
                        curve_value(curve_id),
                        None if duration_curve is None else curve_value(duration_curve),
                        cooldown,
                        limit,
                        curve_id,
                        self.source_row_id("abyss_buff_curves", curve_id),
                    ),
                )

    def _import_official_activity_catalog(self) -> None:
        category_by_type: dict[str, str] = {}
        for ordinal, (row_id, row) in enumerate(
            sorted(
                self.rows["clone_overview"].items(),
                key=lambda item: int(item[0]),
            )
        ):
            name, _, _ = resolved_text_parts(self.rows, row.get("TypeText"))
            for clone_type in row.get("CloneType") or ():
                type_name = enum_tail(clone_type) or str(clone_type)
                category_id = f"clone:{type_name.casefold()}"
                category_by_type[type_name] = category_id
                self.connection.execute(
                    "INSERT INTO clone_activity_category VALUES (?,?,?,?,?)",
                    (
                        category_id,
                        type_name,
                        name or type_name,
                        ordinal,
                        self.source_row_id("clone_overview", row_id),
                    ),
                )

        for clone_id, row in sorted(self.rows["clone_system"].items()):
            clone_type = enum_tail(row.get("CloneType")) or "Default"
            show_in_adventure = bool(row.get("bShowInAdventure")) and not re.search(
                r"(?:^|_)test(?:_|$)",
                clone_id,
                re.IGNORECASE,
            )
            name, _, _ = resolved_text_parts(self.rows, row.get("CloneName"))
            description = None
            nodes = row.get("CloneSystemSubNodes") or ()
            for node in nodes:
                descriptions = node.get("CloneDesArray") or ()
                if descriptions:
                    description, _, _ = resolved_text_parts(
                        self.rows,
                        descriptions[0].get("CloneDesBody"),
                    )
                if description:
                    break
            self.connection.execute(
                "INSERT INTO clone_activity VALUES (?,?,?,?,?,?,?,?)",
                (
                    clone_id,
                    clone_type,
                    category_by_type.get(clone_type),
                    name or clone_id,
                    description,
                    bool_int(show_in_adventure),
                    bool_int(row.get("bCrossScene")),
                    self.source_row_id("clone_system", clone_id),
                ),
            )
            for difficulty_ordinal, node in enumerate(nodes):
                time_limit = float(node.get("KillMonsterTimeLimit") or 0.0)
                self.connection.execute(
                    "INSERT INTO clone_activity_difficulty VALUES (?,?,?,?,?,?,?,?)",
                    (
                        clone_id,
                        difficulty_ordinal,
                        int(node.get("DifficultyLevel") or 0),
                        int(node.get("TeamLevel") or 0),
                        int(node.get("StaminaCount") or 0),
                        optional_text(node.get("DropID")),
                        optional_text(node.get("SpawnID")),
                        time_limit if time_limit >= 0.0 else None,
                    ),
                )

        self._import_clone_spawn_members()
        self._import_monster_template_bindings()

    def _import_outer_realm_rotations(self) -> None:
        rotations_by_config: dict[str, tuple[str, str, int]] = {}
        for quest_id, row in self.rows["combat_award_quests"].items():
            objective = row.get("ObjectiveInfo") or {}
            config_id = optional_text(objective.get("AbyssID"))
            start = _mainland_timestamp(row.get("QuestStartTime"))
            end = _mainland_timestamp(row.get("QuestEndTime"))
            if (
                not row.get("bTimeLimitQuest")
                or enum_tail(objective.get("ObjectiveType")) != "AbyssCompleted"
                or not config_id
                or config_id == "None"
                or not start
                or not end
            ):
                continue
            rotation = (
                start,
                end,
                self.source_row_id("combat_award_quests", quest_id),
            )
            previous = rotations_by_config.get(config_id)
            if previous is not None and previous[:2] != rotation[:2]:
                raise StaticDatabaseError(
                    f"轨外配置存在冲突的大陆服生效区间：{config_id}"
                )
            rotations_by_config[config_id] = rotation
        rotations = [
            (config_id, *values)
            for config_id, values in rotations_by_config.items()
        ]
        # The static dataset's ``as_of`` is an effective Mainland calendar day,
        # i.e. data built for that day's post-reset game state.  A rotation
        # ending during the reset window on the same date is therefore already
        # historical and must not displace the current + next pair.
        cutoff_date = self.as_of.isoformat()
        inference_ids = {
            row[0]: ordinal
            for ordinal, row in enumerate(
                sorted(
                    (row for row in rotations if row[2][:10] > cutoff_date),
                    key=lambda item: item[1],
                )[:2]
            )
        }
        for config_id, start, end, source_row_id in rotations:
            self.connection.execute(
                "INSERT OR REPLACE INTO outer_realm_rotation VALUES (?,?,?,?,?)",
                (
                    config_id,
                    start,
                    end,
                    inference_ids.get(config_id),
                    source_row_id,
                ),
            )

    def _import_clone_spawn_members(self) -> None:
        for spawn_id, row in sorted(self.rows["clone_monster_config"].items()):
            source_row_id = self.source_row_id("clone_monster_config", spawn_id)
            for wave_ordinal, wave in enumerate(
                row.get("SpawnWaveEntriesOverride") or ()
            ):
                for entry_ordinal, entry in enumerate(wave.get("SpawnEntries") or ()):
                    path = asset_path(entry.get("AITemplate")) or ""
                    if not path:
                        continue
                    leaf = path.rsplit("/", 1)[-1].split(".", 1)[0]
                    template_name = leaf.removesuffix("_C")
                    self.connection.execute(
                        "INSERT INTO clone_spawn_member VALUES (?,?,?,?,?,?,?)",
                        (
                            spawn_id,
                            wave_ordinal,
                            entry_ordinal,
                            path,
                            template_name,
                            max(1, int(entry.get("Count") or 1)),
                            source_row_id,
                        ),
                    )

    def _import_monster_template_bindings(self) -> None:
        manual_by_tag = {
            str(row.get("MonsterTag")): monster_id
            for monster_id, row in self.rows["monster_manual"].items()
            if optional_text(row.get("MonsterTag"))
        }
        for template_id, row in sorted(self.rows["monster_tags"].items()):
            template_name = str(row.get("AITemplateName") or template_id)
            for monster_tag in row.get("MonsterTags") or ():
                manual_id = manual_by_tag.get(str(monster_tag))
                if manual_id:
                    self._insert_template_binding(
                        template_name,
                        manual_id,
                        "monster_tag",
                        self.source_row_id("monster_tags", template_id),
                    )

        manual_by_key: dict[str, list[str]] = {}
        for monster_id, row in self.rows["monster_manual"].items():
            key = _monster_identity_key(monster_id)
            if key:
                manual_by_key.setdefault(key, []).append(monster_id)
            world_boss_id = optional_text(row.get("WorldBossID"))
            if world_boss_id:
                self._insert_template_binding(
                    world_boss_id,
                    monster_id,
                    "world_boss_id",
                    self.source_row_id("monster_manual", monster_id),
                )

        for row in self.connection.execute(
            "SELECT DISTINCT monster_template_name, source_row_id FROM clone_spawn_member"
        ):
            matches = manual_by_key.get(_monster_identity_key(row[0]), ())
            if len(matches) == 1:
                self._insert_template_binding(
                    str(row[0]),
                    matches[0],
                    "numeric_hint",
                    int(row[1]),
                )

    def _insert_template_binding(
        self,
        template_name: str,
        monster_manual_id: str,
        binding_kind: str,
        source_row_id: int,
    ) -> None:
        self.connection.execute(
            "INSERT OR IGNORE INTO monster_template_binding VALUES (?,?,?,?)",
            (template_name, monster_manual_id, binding_kind, source_row_id),
        )


def _monster_identity_key(value: object) -> str:
    match = re.search(r"(?i)(boss|mon)_0*(\d+)", str(value or ""))
    if match is None:
        return ""
    return f"{match.group(1).lower()}_{int(match.group(2))}"
