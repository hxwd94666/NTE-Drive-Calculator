# 导入弧盘、战斗环境、敌人、怪物实例和深渊绑定。
"""Static database builder responsibility mixin."""

from __future__ import annotations

from tools.game_data.static_database_build_support import *


class CombatImportMixin:
    def _import_forks(self) -> None:
        canonical_star_packs: dict[str, str] = {}
        for row_key in self.rows["fork_stars"]:
            pack_id, _star_level = split_numbered_row(row_key)
            normalized = pack_id.casefold()
            previous = canonical_star_packs.setdefault(normalized, pack_id)
            if previous != pack_id:
                raise StaticDatabaseError(
                    f"弧盘精炼包仅大小写不同且无法唯一解析：{previous}/{pack_id}"
                )
        canonical_breakthrough_packs: dict[str, str] = {}
        breakthrough_stages: dict[str, set[int]] = {}
        for row_key in self.rows["fork_breakthroughs"]:
            pack_id, stage = split_numbered_row(row_key)
            normalized = pack_id.casefold()
            previous = canonical_breakthrough_packs.setdefault(normalized, pack_id)
            if previous != pack_id:
                raise StaticDatabaseError(
                    f"弧盘突破包仅大小写不同且无法唯一解析：{previous}/{pack_id}"
                )
            breakthrough_stages.setdefault(normalized, set()).add(int(stage))
        for type_id in sorted(self.rows["fork_types"], key=int):
            row = self.rows["fork_types"][type_id]
            name, _, _ = text_parts(row.get("TypeName"))
            description, _, _ = text_parts(row.get("DetailContent"))
            if name is None:
                raise StaticDatabaseError(f"弧盘类型没有名称：{type_id}")
            self.connection.execute(
                "INSERT INTO fork_type VALUES (?,?,?,?,?)",
                (
                    int(type_id),
                    name,
                    description,
                    asset_path(row.get("TypeIcon")),
                    self.source_row_id("fork_types", type_id),
                ),
            )
        for fork_id in sorted(self.rows["fork_items"]):
            row = self.rows["fork_items"][fork_id]
            element = row.get("ElementData")
            if not isinstance(element, dict):
                raise StaticDatabaseError(f"弧盘缺少 ElementData：{fork_id}")
            name, text_table, text_key = text_parts(row.get("ItemName"))
            description, _, _ = text_parts(row.get("Description"))
            group_type = element.get("ApplyGroupType")
            quality = enum_tail(row.get("ItemQuality"), "ITEM_QUALITY_")
            if name is None or quality is None:
                raise StaticDatabaseError(f"弧盘身份字段不完整：{fork_id}")
            raw_star_pack_id = optional_text(element.get("UpgradeStarPackID"))
            star_pack_id = (
                canonical_star_packs.get(raw_star_pack_id.casefold())
                if raw_star_pack_id is not None
                else None
            )
            if int(element.get("MaxUpgradeStar") or 0) > 0 and star_pack_id is None:
                raise StaticDatabaseError(
                    f"弧盘精炼包无法解析：{fork_id}/{raw_star_pack_id}"
                )
            raw_breakthrough_pack_id = optional_text(
                element.get("BreakthroughPackId")
            )
            breakthrough_pack_id = (
                canonical_breakthrough_packs.get(
                    raw_breakthrough_pack_id.casefold()
                )
                if raw_breakthrough_pack_id is not None
                else None
            )
            maximum_breakthrough = int(element.get("MaxBreakthrough") or 0)
            if maximum_breakthrough > 0 and breakthrough_pack_id is None:
                raise StaticDatabaseError(
                    f"弧盘突破包无法解析：{fork_id}/{raw_breakthrough_pack_id}"
                )
            actual_stages = breakthrough_stages.get(
                (raw_breakthrough_pack_id or "").casefold(), set()
            )
            expected_stages = set(range(maximum_breakthrough + 1))
            if maximum_breakthrough > 0 and actual_stages != expected_stages:
                raise StaticDatabaseError(
                    f"弧盘突破档不完整：{fork_id}/"
                    f"{sorted(actual_stages)} != {sorted(expected_stages)}"
                )
            self.connection.execute(
                "INSERT INTO fork_item VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    fork_id,
                    name,
                    text_table,
                    text_key,
                    description,
                    quality,
                    FORK_TYPE_ID_BY_CHARACTER_GROUP.get(group_type),
                    group_type,
                    element.get("UpgradePackId"),
                    breakthrough_pack_id,
                    star_pack_id,
                    maximum_breakthrough,
                    element.get("MaxUpgradeStar"),
                    asset_path(row.get("ItemIcon")),
                    asset_path(element.get("ForkCard")),
                    asset_path(element.get("OriginalPainting")),
                    canonical_json(element.get("ExclusiveCharacterIDArray", [])),
                    self.source_row_id("fork_items", fork_id),
                ),
            )
        for row_key in sorted(self.rows["fork_upgrades"]):
            pack_id, level = split_numbered_row(row_key)
            row = self.rows["fork_upgrades"][row_key]
            self.connection.execute(
                "INSERT INTO fork_upgrade_level VALUES (?,?,?,?,?)",
                (
                    pack_id,
                    level,
                    int(row["NeedExp"]),
                    row["ModifyPack"],
                    self.source_row_id("fork_upgrades", row_key),
                ),
            )
        for modify_pack_id in sorted(self.rows["fork_modify"]):
            row = self.rows["fork_modify"][modify_pack_id]
            self.connection.execute(
                "INSERT INTO fork_modify_pack VALUES (?,?,?)",
                (
                    modify_pack_id,
                    canonical_json(row.get("ConditionArray", [])),
                    self.source_row_id("fork_modify", modify_pack_id),
                ),
            )
            for ordinal, value in enumerate(row.get("ModifyData", [])):
                self.connection.execute(
                    "INSERT INTO fork_modify_value VALUES (?,?,?,?,?,?)",
                    (
                        modify_pack_id,
                        ordinal,
                        value["PropName"],
                        float(value["PropValue"]),
                        enum_tail(value.get("ModifierOp")) or "",
                        value.get("SortKey"),
                    ),
                )
        for row_key in sorted(self.rows["fork_breakthroughs"]):
            pack_id, stage = split_numbered_row(row_key)
            row = self.rows["fork_breakthroughs"][row_key]
            self.connection.execute(
                "INSERT INTO fork_breakthrough VALUES (?,?,?,?,?,?,?)",
                (
                    pack_id,
                    stage,
                    int(row["MaxForkLevel"]),
                    row.get("NeedItems"),
                    row.get("NeedGolds"),
                    row.get("ModifyPackID"),
                    self.source_row_id("fork_breakthroughs", row_key),
                ),
            )
        for row_key in sorted(self.rows["fork_stars"]):
            pack_id, star_level = split_numbered_row(row_key)
            row = self.rows["fork_stars"][row_key]
            title, _, _ = text_parts(row.get("Title"))
            description, _, _ = text_parts(row.get("Description"))
            self.connection.execute(
                "INSERT INTO fork_star_level VALUES (?,?,?,?,?,?,?)",
                (
                    pack_id,
                    star_level,
                    title,
                    description,
                    row.get("NeedGolds"),
                    canonical_json(row.get("Buffs", [])),
                    self.source_row_id("fork_stars", row_key),
                ),
            )
            for ordinal, parameter in enumerate(row.get("DataList", [])):
                self.connection.execute(
                    "INSERT INTO fork_star_parameter VALUES (?,?,?,?,?)",
                    (
                        pack_id,
                        star_level,
                        ordinal,
                        parameter["NameID"],
                        bool_int(parameter.get("bIsPercent")),
                    ),
                )
        for name_id in sorted(self.rows["fork_buff_curves"]):
            curve = self.rows["fork_buff_curves"][name_id]
            keys = sorted(
                (
                    (float(point["Time"]), float(point["Value"]))
                    for point in curve.get("Keys", [])
                ),
                key=lambda point: point[0],
            )
            if not keys:
                raise StaticDatabaseError(
                    f"弧盘精炼参数曲线没有数值点：{name_id}"
                )
            for refinement_level in range(1, 6):
                value = keys[0][1]
                for time, candidate in keys:
                    if time > refinement_level:
                        break
                    value = candidate
                self.connection.execute(
                    """
                    INSERT INTO fork_refinement_parameter_value(
                        name_id, refinement_level, value, source_row_id
                    ) VALUES (?,?,?,?)
                    """,
                    (
                        name_id,
                        refinement_level,
                        value,
                        self.source_row_id("fork_buff_curves", name_id),
                    ),
                )

    def _import_combat_context(self) -> None:
        topple_row_id = "UnbaldamagePara"
        topple_row = self.rows["combat_global_curves"].get(topple_row_id)
        if not isinstance(topple_row, dict):
            raise StaticDatabaseError("全局战斗曲线缺少 UnbaldamagePara")
        topple_points = topple_row.get("Keys")
        if not isinstance(topple_points, list) or not topple_points:
            raise StaticDatabaseError("倾陷等级乘区没有曲线点")
        self.connection.execute(
            "INSERT INTO combat_level_curve VALUES (?,?,?,?,?,?,?)",
            (
                "topple:character_level", "topple", None, None,
                enum_tail(topple_row.get("InterpMode")), "exact_level",
                self.source_row_id("combat_global_curves", topple_row_id),
            ),
        )
        for ordinal, point in enumerate(topple_points):
            self.connection.execute(
                "INSERT INTO combat_level_curve_point VALUES (?,?,?,?,?)",
                ("topple:character_level", ordinal, float(point["Time"]), None, float(point["Value"])),
            )

        for effect_id in sorted(self.rows["reaction_damage"]):
            row = self.rows["reaction_damage"][effect_id]
            values = row.get("ReactionDamageArray")
            if not isinstance(values, list) or not values:
                raise StaticDatabaseError(f"环合伤害缺少官方档位数组：{effect_id}")
            reaction_type = enum_tail(row.get("ProduceReactionType"))
            curve_id = f"reaction:{effect_id}"
            self.connection.execute(
                "INSERT INTO combat_level_curve VALUES (?,?,?,?,?,?,?)",
                (
                    curve_id, "reaction", reaction_type, effect_id, None,
                    "source_tier", self.source_row_id("reaction_damage", effect_id),
                ),
            )
            for source_tier, value in enumerate(values):
                self.connection.execute(
                    "INSERT INTO combat_level_curve_point VALUES (?,?,?,?,?)",
                    (curve_id, source_tier, None, source_tier, float(value)),
                )

        for reaction_type in sorted(self.rows["reaction_definitions"]):
            row = self.rows["reaction_definitions"][reaction_type]
            official_type = enum_tail(row.get("ReactionResult")) or reaction_type
            element_type_1 = enum_tail(row.get("CharacterElementType1"))
            element_type_2 = enum_tail(row.get("CharacterElementType2"))
            if element_type_1 is None or element_type_2 is None:
                raise StaticDatabaseError(f"环合缺少元素组合：{reaction_type}")
            self.connection.execute(
                "INSERT INTO reaction_definition VALUES (?,?,?,?,?)",
                (
                    official_type, element_type_1, element_type_2,
                    optional_text(row.get("DefaultDamageGE")),
                    self.source_row_id("reaction_definitions", reaction_type),
                ),
            )

        for constant_id in sorted(self.rows["reaction_constants"]):
            row = self.rows["reaction_constants"][constant_id]
            keys = row.get("Keys")
            if not isinstance(keys, list) or len(keys) != 1:
                raise StaticDatabaseError(f"环合常量必须恰好包含一个官方曲线点：{constant_id}")
            unit, description = REACTION_CONSTANT_METADATA.get(constant_id, ("scalar", None))
            point = keys[0]
            self.connection.execute(
                "INSERT INTO combat_effect_constant VALUES (?,?,?,?,?,?)",
                (
                    constant_id, float(point["Time"]), float(point["Value"]), unit,
                    description, self.source_row_id("reaction_constants", constant_id),
                ),
            )

    def _import_enemy_combat_profiles(self) -> None:
        for table_name, profile_set in (
            ("monster_pack", "standard"),
            ("monster_pack_night_999", "night_999"),
        ):
            for pack_id in sorted(self.rows[table_name]):
                row = self.rows[table_name][pack_id]
                self.connection.execute(
                    """
                    INSERT INTO enemy_combat_profile(
                        profile_set, pack_id, defense_base, defense_up,
                        defense_add, defense_ignore, topple_limit,
                        topple_accrue_efficiency, topple_anti_accrue_efficiency,
                        topple_bonus, topple_reduce_natural, topple_reduce_reset,
                        source_row_id, health_base, health_up, health_add
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        profile_set, pack_id, float_value(row, "DefBase"),
                        float_value(row, "DefUp"), float_value(row, "DefAdd"),
                        float_value(row, "DefIgnore"), float_value(row, "UnbalMax"),
                        float_value(row, "UnbalAccrueEfficiencyBase"),
                        float_value(row, "UnbalAntiAccrueEfficiencyBase"),
                        float_value(row, "UnbaleBonus"), float_value(row, "UnbalReduceNatur"),
                        float_value(row, "UnbalReduceReset"),
                        self.source_row_id(table_name, pack_id),
                        float_value(row, "HPMaxBase"),
                        float_value(row, "HPMaxUp"),
                        float_value(row, "HPMaxAdd"),
                    ),
                )
                for damage_type, (resistance_field, immunity_field) in ENEMY_RESISTANCE_FIELDS.items():
                    self.connection.execute(
                        "INSERT INTO enemy_element_resistance VALUES (?,?,?,?,?)",
                        (
                            profile_set, pack_id, damage_type,
                            float_value(row, resistance_field), float_value(row, immunity_field),
                        ),
                    )

    def _import_roguelike_modifiers(self) -> None:
        for modifier_id in sorted(self.rows["roguelike_modify"]):
            row = self.rows["roguelike_modify"][modifier_id]
            conditions = row.get("ConditionArray") or []
            modifiers = row.get("ModifyData") or []
            if not isinstance(conditions, list) or not isinstance(modifiers, list):
                raise StaticDatabaseError(
                    f"RogueLike 修正配置字段不是数组：{modifier_id}"
                )
            self.connection.execute(
                "INSERT INTO roguelike_modifier_profile VALUES (?,?,?)",
                (
                    modifier_id,
                    canonical_json(conditions),
                    self.source_row_id("roguelike_modify", modifier_id),
                ),
            )
            for ordinal, modifier in enumerate(modifiers):
                if not isinstance(modifier, dict):
                    raise StaticDatabaseError(
                        f"RogueLike 修正条目不是对象：{modifier_id}/{ordinal}"
                    )
                property_id = optional_text(modifier.get("PropName"))
                if property_id is None:
                    raise StaticDatabaseError(
                        f"RogueLike 修正条目缺少属性：{modifier_id}/{ordinal}"
                    )
                self.connection.execute(
                    "INSERT INTO roguelike_modifier_property VALUES (?,?,?,?,?,?)",
                    (
                        modifier_id,
                        ordinal,
                        property_id,
                        optional_text(modifier.get("ModifierOp")) or "unknown",
                        float_value(modifier, "PropValue"),
                        optional_int(modifier.get("SortKey")) or 0,
                    ),
                )

    def _import_monster_instance_profiles(self) -> None:
        """只导入静态表中的显式绑定；FT_ 是 999 夜前缀，不用于判断 Abyss。"""
        variant_fields = (
            ("world_level", "WorldLevelArray", "MonsterWorldLevel", "MonsterWorldLevelPropModifyID"),
            ("clone_level", "CloneDifficultyLevelArray", "MonsterCloneLevel", "MonsterClonePropModifyID"),
            ("abyss_level", "AbyssCloneLevelArray", "MonsterAbyssLevel", "MonsterAbyssPropModifyID"),
        )
        for table_name in sorted(name for name in self.rows if name.startswith("monster_static_")):
            for monster_id in sorted(self.rows[table_name]):
                row = self.rows[table_name][monster_id]
                self.connection.execute(
                    "INSERT INTO monster_instance_profile VALUES (?,?,?,?,?,?,?)",
                    (
                        table_name, monster_id, optional_int(row.get("MonsterLevel")) or 0,
                        "standard", optional_text(row.get("PropModifyID")),
                        optional_text(row.get("OnlineRatioID")),
                        self.source_row_id(table_name, monster_id),
                    ),
                )
                for variant_kind, array_field, level_field, pack_field in variant_fields:
                    variants = row.get(array_field, [])
                    if not isinstance(variants, list):
                        raise StaticDatabaseError(f"怪物属性包变体字段不是数组：{monster_id}/{array_field}")
                    for variant in variants:
                        if not isinstance(variant, dict):
                            raise StaticDatabaseError(f"怪物属性包变体不是对象：{monster_id}/{array_field}")
                        pack_id = optional_text(variant.get(pack_field))
                        level = optional_int(variant.get(level_field))
                        if pack_id is not None and level is not None:
                            self.connection.execute(
                                "INSERT OR IGNORE INTO monster_instance_profile_variant VALUES (?,?,?,?,?,?)",
                                (table_name, monster_id, variant_kind, level, "standard", pack_id),
                            )

    def _import_abyss_bindings(self) -> None:
        """导入明确的 Abyss 关卡 → 怪物池 → 怪物 → 普通属性包链。"""
        for level_config_id in sorted(self.rows["abyss_clone_levels"]):
            source_row_id = self.source_row_id("abyss_clone_levels", level_config_id)
            levels = self.rows["abyss_clone_levels"][level_config_id].get("LevelConfigArray", [])
            if not isinstance(levels, list):
                raise StaticDatabaseError(f"Abyss 关卡配置不是数组：{level_config_id}")
            for level in levels:
                if not isinstance(level, dict):
                    raise StaticDatabaseError(f"Abyss 关卡配置不是对象：{level_config_id}")
                level_id = optional_int(level.get("LevelID"))
                if level_id is None:
                    raise StaticDatabaseError(f"Abyss 关卡缺少 LevelID：{level_config_id}")
                name_zh, _, _ = text_parts(level.get("LevelName"))
                self.connection.execute(
                    "INSERT INTO abyss_level VALUES (?,?,?,?,?)",
                    (level_config_id, level_id, optional_text(level.get("AbyssID")), name_zh, source_row_id),
                )
                stages = level.get("SpawnMonsterConfigMap", [])
                if not isinstance(stages, list):
                    raise StaticDatabaseError(f"Abyss 波次配置不是数组：{level_config_id}/{level_id}")
                for stage in stages:
                    if not isinstance(stage, dict) or not isinstance(stage.get("Value"), dict):
                        raise StaticDatabaseError(f"Abyss 波次配置无效：{level_config_id}/{level_id}")
                    spawns = stage["Value"].get("CloneSpawnMonsterConfigArray", [])
                    if not isinstance(spawns, list):
                        raise StaticDatabaseError(f"Abyss 生成配置不是数组：{level_config_id}/{level_id}")
                    fight_stage = optional_text(stage.get("Key")) or "unknown"
                    for ordinal, spawn in enumerate(spawns):
                        if not isinstance(spawn, dict):
                            raise StaticDatabaseError(f"Abyss 生成配置不是对象：{level_config_id}/{level_id}")
                        monster_pool_id = optional_text(spawn.get("MonsterPoolID"))
                        if monster_pool_id is None:
                            raise StaticDatabaseError(f"Abyss 生成配置缺少 MonsterPoolID：{level_config_id}/{level_id}")
                        self.connection.execute(
                            "INSERT INTO abyss_level_monster_spawn VALUES (?,?,?,?,?,?,?,?,?)",
                            (
                                level_config_id, level_id, fight_stage, ordinal,
                                optional_int(spawn.get("Wave")), monster_pool_id,
                                optional_text(spawn.get("NextSpawnType")),
                                float_value(spawn, "SpawnTime"), source_row_id,
                            ),
                        )

        standard_profiles = self.rows["monster_pack"]
        for monster_pool_id in sorted(self.rows["abyss_monster_pools"]):
            pool_source_row_id = self.source_row_id("abyss_monster_pools", monster_pool_id)
            monsters = self.rows["abyss_monster_pools"][monster_pool_id].get("MonsterPoolArray", [])
            if not isinstance(monsters, list):
                raise StaticDatabaseError(f"Abyss 怪物池不是数组：{monster_pool_id}")
            for ordinal, monster in enumerate(monsters):
                if not isinstance(monster, dict):
                    raise StaticDatabaseError(f"Abyss 怪物池条目不是对象：{monster_pool_id}")
                attribute_pack_id = optional_text(monster.get("AttributeID"))
                if attribute_pack_id is None or attribute_pack_id not in standard_profiles:
                    raise StaticDatabaseError(
                        f"Abyss 属性包未在 DT_MonsterPackData 中找到：{monster_pool_id}/{attribute_pack_id}"
                    )
                monster_level = optional_int(monster.get("MonsterLevel"))
                monster_count = optional_int(monster.get("MonsterCount"))
                if monster_level is None or monster_count is None:
                    raise StaticDatabaseError(f"Abyss 怪物缺少等级或数量：{monster_pool_id}")
                monster_name_zh, _, _ = text_parts(monster.get("MonsterName"))
                self.connection.execute(
                    "INSERT INTO abyss_monster_pool_entry VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        monster_pool_id, ordinal, asset_path(monster.get("MonsterClass")),
                        monster_count, monster_level, "standard", attribute_pack_id,
                        pool_source_row_id, self.source_row_id("monster_pack", attribute_pack_id),
                        monster_name_zh,
                    ),
                )

    def _database_counts(self) -> dict[str, int]:
        tables = (
            "source_file",
            "source_row",
            "character",
            "character_annotation",
            "character_awaken_effect",
            "character_awaken_skill_level_bonus",
            "character_likeability_bonus",
            "character_likeability_bonus_property",
            "character_panel_growth",
            "character_skill",
            "character_skill_level",
            "skill_damage",
            "skill_damage_modifier",
            "combat_level_curve",
            "combat_level_curve_point",
            "reaction_definition",
            "combat_effect_constant",
            "enemy_combat_profile",
            "enemy_element_resistance",
            "roguelike_modifier_profile",
            "roguelike_modifier_property",
            "monster_instance_profile",
            "monster_instance_profile_variant",
            "abyss_level",
            "abyss_level_monster_spawn",
            "abyss_monster_pool_entry",
            "equipment_attribute",
            "equipment_shape",
            "equipment_shape_cell",
            "equipment_suit",
            "equipment_suit_effect",
            "equipment_item",
            "equipment_plan",
            "character_weight_recommendation",
            "character_weight_recommendation_property",
            "character_graduation_template",
            "application_setting_default",
            "character_shape_bonus",
            "character_shape_bonus_property",
            "logical_character_shape_bonus",
            "logical_character_shape_bonus_property",
            "fork_type",
            "fork_item",
            "fork_upgrade_level",
            "fork_modify_pack",
            "fork_breakthrough",
            "fork_star_level",
            "fork_refinement_parameter_value",
            "character_cultivation_guide",
            "character_cultivation_fork_recommendation",
            "character_cultivation_attribute_recommendation",
            "character_cultivation_stage",
            "character_cultivation_stage_skill",
            "gameplay_ability_catalog",
            "gameplay_ability_description",
            "gameplay_ability_level_hint",
            "gameplay_effect_catalog",
            "monster_catalog",
            "monster_identifier_alias",
            "equipment_modify_pack",
            "equipment_modify_value",
            "equipment_buff_curve",
            "equipment_buff_curve_point",
            "combat_curve",
            "combat_curve_point",
            "combat_effect_definition",
            "combat_blueprint_asset",
            "character_combat_ability_binding",
            "combat_blueprint_reference",
            "combat_blueprint_tag",
            "combat_blueprint_semantic_property",
            "combat_ability_montage_binding",
            "combat_ability_effect_binding",
            "combat_montage",
            "combat_montage_section",
            "combat_montage_notify",
            "buff_definition",
            "buff_modifier",
            "buff_trigger_effect",
            "combat_effect_buff_link",
            "feast_stage",
            "feast_stage_difficulty",
            "feast_option",
            "feast_stage_option",
            "divination_buff",
            "clone_activity_category",
            "clone_activity",
            "clone_activity_difficulty",
            "clone_spawn_member",
            "monster_template_binding",
            "outer_realm_rotation",
            "high_risk_commission",
            "high_risk_commission_difficulty",
            "high_risk_monster_pool_member",
            "monster_boss_support",
            "character_release_evidence",
            "character_release_annotation",
            "character_release_evidence_link",
            "character_acquisition_membership",
            "localized_term",
            "localized_term_name",
            "fork_lottery_campaign",
            "damage_resistance_term",
            "progression_item",
            "progression_item_alias",
            "item_quality_term",
            "clone_drop_projection",
            "clone_drop_projection_item",
            "clone_drop_projection_gap",
        )
        return {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }
