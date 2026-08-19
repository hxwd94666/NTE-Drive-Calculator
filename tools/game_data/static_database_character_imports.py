# 导入角色、成长、觉醒、技能和技能伤害静态数据。
"""Static database builder responsibility mixin."""

from __future__ import annotations

from tools.game_data.static_database_build_support import *


class CharacterImportMixin:
    def source_row_id(self, table: str, row_key: str) -> int:
        try:
            return self.source_row_ids[(table, str(row_key))]
        except KeyError as exc:
            raise StaticDatabaseError(f"缺少已镜像的来源记录：{table}/{row_key}") from exc

    def _mirror_sources(self) -> None:
        for source_file_id, table in enumerate(sorted(TABLE_PATHS), start=1):
            relative_path = TABLE_PATHS[table]
            path = self.content_root / Path(relative_path)
            if not path.is_file():
                raise StaticDatabaseError(f"缺少必要的来源文件：{path}")
            _, rows = load_datatable(path)
            self.rows[table] = rows
            self.connection.execute(
                "INSERT INTO source_file VALUES (?, ?, ?, ?)",
                (source_file_id, relative_path, file_sha256(path), len(rows)),
            )
            for row_key in sorted(rows):
                payload_json = canonical_json(rows[row_key])
                cursor = self.connection.execute(
                    "INSERT INTO source_row(source_file_id,row_key,payload_json,content_sha256) "
                    "VALUES (?,?,?,?)",
                    (
                        source_file_id,
                        str(row_key),
                        payload_json if self.include_source_payloads else None,
                        sha256_bytes(payload_json.encode("utf-8")),
                    ),
                )
                self.source_row_ids[(table, str(row_key))] = int(cursor.lastrowid)

    def _import_characters(self) -> None:
        catalog = build_character_catalog(
            self.content_root,
            None,
            self.overrides_path,
            as_of=self.as_of,
        )
        source_rows = self.rows["character"]
        annotations = []
        for item in catalog["characters"]:
            character_id = item["character_id"]
            row = source_rows[character_id]
            name, text_table, text_key = text_parts(row.get("ItemName"))
            if name is None:
                raise StaticDatabaseError(f"角色在官方数据中没有名称：{character_id}")
            canonical_id = item.get("canonical_character_id")
            self.connection.execute(
                "INSERT INTO character VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    int(character_id),
                    name,
                    text_table,
                    text_key,
                    item.get("element_type"),
                    item.get("group_type"),
                    item.get("actor_class"),
                    show_time(row),
                    self.source_row_id("character", character_id),
                ),
            )
            annotations.append(
                (
                    int(character_id),
                    item["logical_character_key"],
                    int(canonical_id) if canonical_id is not None else None,
                    item["classification"],
                    self.overrides_path.name,
                )
            )
        self.connection.executemany(
            "INSERT INTO character_annotation VALUES (?,?,?,?,?)",
            annotations,
        )

    def _mirror_awaken_sources(self) -> None:
        """镜像每个角色独立的觉醒表，并按表内角色 ID 建立索引。"""

        directory = self.content_root / AWAKEN_DIRECTORY
        if not directory.is_dir():
            raise StaticDatabaseError(f"缺少角色觉醒目录：{directory}")

        source_file_id = len(TABLE_PATHS)
        paths = sorted(directory.glob("*AwakenEffect*.json"))
        if not paths:
            raise StaticDatabaseError(f"角色觉醒目录没有 AwakenEffect 数据：{directory}")
        for path in paths:
            _, rows = load_datatable(path)
            source_file_id += 1
            relative_path = path.relative_to(self.content_root).as_posix()
            self.connection.execute(
                "INSERT INTO source_file VALUES (?, ?, ?, ?)",
                (source_file_id, relative_path, file_sha256(path), len(rows)),
            )
            for row_key in sorted(rows):
                try:
                    character_id = int(row_key)
                except (TypeError, ValueError) as exc:
                    raise StaticDatabaseError(
                        f"角色觉醒记录键必须是角色 ID：{relative_path}/{row_key}"
                    ) from exc
                if character_id in self.awaken_rows:
                    raise StaticDatabaseError(f"角色存在重复觉醒定义：{character_id}")
                row = rows[row_key]
                payload_json = canonical_json(row)
                cursor = self.connection.execute(
                    "INSERT INTO source_row(source_file_id,row_key,payload_json,content_sha256) "
                    "VALUES (?,?,?,?)",
                    (
                        source_file_id,
                        str(row_key),
                        payload_json if self.include_source_payloads else None,
                        sha256_bytes(payload_json.encode("utf-8")),
                    ),
                )
                self.awaken_rows[character_id] = (row, int(cursor.lastrowid))

    def _import_character_awakens(self) -> None:
        """导入六觉与三/六觉共鸣；用户的选择状态属于账号私有数据。"""

        character_rows = self.rows["character"]
        for character_id, character, classification in self.connection.execute(
            """
            SELECT c.character_id, c.name_zh, a.classification
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            ORDER BY c.character_id
            """
        ):
            if classification == "combat_transformation":
                continue
            source_character = character_rows.get(str(character_id))
            if not isinstance(source_character, dict):
                raise StaticDatabaseError(f"角色缺少原始记录：{character_id}")
            max_awaken = int(source_character.get("MaxAwakenLevel") or 0)
            source = self.awaken_rows.get(character_id)
            if source is None:
                if max_awaken > 0:
                    raise StaticDatabaseError(f"角色缺少觉醒定义：{character_id}/{character}")
                continue
            row, source_row_id = source
            effects = row.get("AwakenEffectStructList")
            if not isinstance(effects, list) or not effects:
                raise StaticDatabaseError(f"角色觉醒效果为空：{character_id}")
            normal_effects = [effect for effect in effects if str(effect.get("EffectID", "")).startswith("Effect")]
            if max_awaken and len(normal_effects) != max_awaken:
                raise StaticDatabaseError(
                    f"角色觉醒数量不匹配：{character_id} 需要 {max_awaken}，实际 {len(normal_effects)}"
                )
            for ordinal, effect in enumerate(effects):
                if not isinstance(effect, dict):
                    raise StaticDatabaseError(f"角色觉醒效果格式无效：{character_id}/{ordinal}")
                effect_id = effect.get("EffectID")
                if not isinstance(effect_id, str) or not effect_id:
                    raise StaticDatabaseError(f"角色觉醒缺少 EffectID：{character_id}/{ordinal}")
                title, title_table, title_key = text_parts(effect.get("Title"))
                description, description_table, description_key = text_parts(effect.get("Desc"))
                modify_data = effect.get("ModifyDataList", [])
                gameplay_effect_ids = effect.get("GEIdArray", [])
                if not isinstance(modify_data, list) or not isinstance(gameplay_effect_ids, list):
                    raise StaticDatabaseError(f"角色觉醒修改数据无效：{character_id}/{effect_id}")
                self.connection.execute(
                    "INSERT INTO character_awaken_effect VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        character_id,
                        effect_id,
                        ordinal,
                        enum_tail(effect.get("AwakenType")) or "Unknown",
                        title,
                        title_table,
                        title_key,
                        description,
                        description_table,
                        description_key,
                        asset_path(effect.get("AwakenIcon")),
                        canonical_json(modify_data),
                        canonical_json(gameplay_effect_ids),
                        source_row_id,
                    ),
                )
                for skill_ordinal, modifier in enumerate(modify_data):
                    skill_id = modifier.get("SkillName") if isinstance(modifier, dict) else None
                    level_delta = modifier.get("SkillLevel") if isinstance(modifier, dict) else None
                    if skill_id is None and level_delta is None:
                        continue
                    if not isinstance(skill_id, str) or not skill_id or not isinstance(level_delta, int):
                        raise StaticDatabaseError(
                            f"角色觉醒技能等级加成无效：{character_id}/{effect_id}/{skill_ordinal}"
                        )
                    self.connection.execute(
                        "INSERT INTO character_awaken_skill_level_bonus VALUES (?,?,?,?,?)",
                        (character_id, effect_id, skill_ordinal, skill_id, level_delta),
                    )

    def _import_character_likeability_bonuses(self) -> None:
        """Import the formal level-ten panel bonus for each supported role."""

        known_attributes = {
            str(row[0])
            for row in self.connection.execute(
                "SELECT attribute_id FROM equipment_attribute"
            )
        }
        character_ids = [
            int(row[0])
            for row in self.connection.execute(
                "SELECT character_id FROM character ORDER BY character_id"
            )
        ]
        role_rows = self.rows["likeability_roles"]
        modify_rows = self.rows["likeability_modify"]
        for character_id in character_ids:
            role = role_rows.get(str(character_id))
            if not isinstance(role, dict):
                continue
            level_map = role.get("MapModifyData") or []
            if not isinstance(level_map, list):
                raise StaticDatabaseError(f"角色好感度等级映射无效：{character_id}")
            level_ten = [
                row
                for row in level_map
                if isinstance(row, dict) and str(row.get("Key")) == "10"
            ]
            if not level_ten:
                continue
            if len(level_ten) != 1:
                raise StaticDatabaseError(f"角色存在重复好感度 10 级加成：{character_id}")
            modify_data_id = str(level_ten[0].get("Value") or "").strip()
            modifier = modify_rows.get(modify_data_id)
            if not modify_data_id or not isinstance(modifier, dict):
                raise StaticDatabaseError(
                    f"角色好感度 10 级修改包不存在：{character_id}/{modify_data_id}"
                )
            if modifier.get("ConditionArray") != []:
                raise StaticDatabaseError(
                    f"角色好感度 10 级修改包存在未建模条件：{modify_data_id}"
                )
            modifiers = modifier.get("ModifyData")
            if not isinstance(modifiers, list) or not modifiers:
                raise StaticDatabaseError(f"角色好感度修改包为空：{modify_data_id}")
            role_source = self.source_row_id("likeability_roles", str(character_id))
            modifier_source = self.source_row_id(
                "likeability_modify",
                modify_data_id,
            )
            self.connection.execute(
                "INSERT INTO character_likeability_bonus VALUES (?,?,?,?,?)",
                (character_id, 10, modify_data_id, role_source, modifier_source),
            )
            seen_properties: set[str] = set()
            for ordinal, row in enumerate(modifiers):
                if not isinstance(row, dict):
                    raise StaticDatabaseError(f"角色好感度修改项无效：{modify_data_id}")
                property_id = str(row.get("PropName") or "").strip()
                value = row.get("PropValue")
                operation = str(row.get("ModifierOp") or "").strip()
                if (
                    property_id not in known_attributes
                    or property_id in seen_properties
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or operation != ADDITIVE_MODIFIER_OPERATION
                ):
                    raise StaticDatabaseError(
                        f"角色好感度修改项无法标准化：{modify_data_id}/{property_id}"
                    )
                seen_properties.add(property_id)
                self.connection.execute(
                    """
                    INSERT INTO character_likeability_bonus_property
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        character_id,
                        ordinal,
                        property_id,
                        float(value),
                        operation,
                        modifier_source,
                    ),
                )

    @staticmethod
    def _character_panel_values(row: Any, row_key: str) -> dict[str, float]:
        if not isinstance(row, dict):
            raise StaticDatabaseError(f"角色基础属性记录无效：{row_key}")
        values: dict[str, float] = {}
        for property_id in CHARACTER_PANEL_PROPERTIES:
            value = row.get(property_id)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise StaticDatabaseError(f"角色基础属性无效：{row_key}/{property_id}")
            values[property_id] = float(value)
        return values

    @staticmethod
    def _character_panel_modifier_values(row: Any, row_key: str) -> dict[str, float]:
        if not isinstance(row, dict) or row.get("ConditionArray") != []:
            raise StaticDatabaseError(f"角色成长修改条件无效：{row_key}")
        modifiers = row.get("ModifyData")
        if not isinstance(modifiers, list) or len(modifiers) != len(CHARACTER_PANEL_PROPERTIES):
            raise StaticDatabaseError(f"角色成长修改项数量无效：{row_key}")
        values: dict[str, float] = {}
        for modifier in modifiers:
            if not isinstance(modifier, dict):
                raise StaticDatabaseError(f"角色成长修改项无效：{row_key}")
            property_id = modifier.get("PropName")
            value = modifier.get("PropValue")
            if property_id not in CHARACTER_PANEL_PROPERTIES or property_id in values:
                raise StaticDatabaseError(f"角色成长属性无效：{row_key}/{property_id}")
            if modifier.get("ModifierOp") != ADDITIVE_MODIFIER_OPERATION:
                raise StaticDatabaseError(f"角色成长操作不是加法：{row_key}/{property_id}")
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise StaticDatabaseError(f"角色成长数值无效：{row_key}/{property_id}")
            values[property_id] = float(value)
        if set(values) != set(CHARACTER_PANEL_PROPERTIES):
            raise StaticDatabaseError(f"角色成长属性不完整：{row_key}")
        return values

    def _import_character_panel_growth(self) -> None:
        """以官方角色包与累计等级/突破修改表生成可直接查询的面板基础属性。"""

        player_pack_rows = self.rows["player_pack"]
        player_modify_rows = self.rows["player_modify"]
        character_rows = self.rows["character"]
        player_pack_keys = {row_key.casefold(): row_key for row_key in player_pack_rows}
        player_modify_keys = {row_key.casefold(): row_key for row_key in player_modify_rows}
        for character_id, classification in self.connection.execute(
            """
            SELECT c.character_id, a.classification
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            ORDER BY c.character_id
            """
        ):
            # 1056 等战斗变身共用规范角色的养成属性，不能作为独立角色入库。
            if classification == "combat_transformation":
                continue
            character_row = character_rows.get(str(character_id))
            element = character_row.get("ElementData") if isinstance(character_row, dict) else None
            base_row_key = element.get("PropModifyID") if isinstance(element, dict) else None
            if not isinstance(base_row_key, str) or not base_row_key.endswith("_base"):
                raise StaticDatabaseError(f"角色缺少 PropModifyID：{character_id}")
            actual_base_row_key = player_pack_keys.get(base_row_key.casefold())
            if actual_base_row_key is None:
                raise StaticDatabaseError(f"角色基础属性不存在：{character_id}/{base_row_key}")
            base_row = player_pack_rows[actual_base_row_key]
            base_values = self._character_panel_values(base_row, actual_base_row_key)
            code = actual_base_row_key[:-len("_base")]
            levels: dict[int, tuple[dict[str, float], int]] = {}
            stages: dict[int, tuple[dict[str, float], int | None]] = {
                0: ({property_id: 0.0 for property_id in CHARACTER_PANEL_PROPERTIES}, None)
            }
            for level in range(1, CHARACTER_MAX_LEVEL + 1):
                requested_row_key = f"{code}_lv_{level}"
                row_key = player_modify_keys.get(requested_row_key.casefold())
                if row_key is None:
                    raise StaticDatabaseError(f"角色缺少等级成长：{character_id}/{requested_row_key}")
                row = player_modify_rows[row_key]
                levels[level] = (
                    self._character_panel_modifier_values(row, row_key),
                    self.source_row_id("player_modify", row_key),
                )
            for stage in range(1, len(CHARACTER_BREAKTHROUGH_LEVELS) + 1):
                requested_row_key = f"{code}_stage_{stage}"
                row_key = player_modify_keys.get(requested_row_key.casefold())
                if row_key is None:
                    raise StaticDatabaseError(f"角色缺少突破成长：{character_id}/{requested_row_key}")
                row = player_modify_rows[row_key]
                values = self._character_panel_modifier_values(row, row_key)
                previous = stages[stage - 1][0]
                if any(values[property_id] < previous[property_id] for property_id in CHARACTER_PANEL_PROPERTIES):
                    raise StaticDatabaseError(f"角色突破累计属性倒退：{character_id}/{row_key}")
                stages[stage] = (values, self.source_row_id("player_modify", row_key))

            def insert_row(level: int, stage: int, state: str) -> None:
                level_values, level_source_row_id = levels[level]
                stage_values, stage_source_row_id = stages[stage]
                final_values = {
                    property_id: base_values[property_id] + level_values[property_id] + stage_values[property_id]
                    for property_id in CHARACTER_PANEL_PROPERTIES
                }
                self.connection.execute(
                    "INSERT INTO character_panel_growth VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        character_id,
                        level,
                        stage,
                        state,
                        final_values["HPMaxBase"],
                        final_values["AtkBase"],
                        final_values["DefBase"],
                        self.source_row_id("player_pack", actual_base_row_key),
                        level_source_row_id,
                        stage_source_row_id,
                    ),
                )

            for level in range(1, CHARACTER_MAX_LEVEL + 1):
                stage_before = sum(cap < level for cap in CHARACTER_BREAKTHROUGH_LEVELS)
                if level in CHARACTER_BREAKTHROUGH_LEVELS:
                    insert_row(level, stage_before, "breakthrough_before")
                    insert_row(level, stage_before + 1, "breakthrough_after")
                else:
                    insert_row(
                        level,
                        stage_before,
                        "max_level" if level == CHARACTER_MAX_LEVEL else "normal",
                    )

    def _import_character_skills(self) -> None:
        """导入角色技能目录和官方等级解锁/消耗规则。"""

        ability_rows = self.rows["character_abilities"]
        effect_rows = self.rows["character_ability_effects"]
        for character_id, classification in self.connection.execute(
            """
            SELECT c.character_id, a.classification
            FROM character AS c
            LEFT JOIN character_annotation AS a USING (character_id)
            ORDER BY c.character_id
            """
        ):
            if classification == "combat_transformation":
                continue
            row_key = str(character_id)
            row = ability_rows.get(row_key)
            # 角色目录可能提前出现尚未配置可升级技能的未实装角色；保留角色记录，
            # 但不伪造技能目录。
            if row is None:
                continue
            if not isinstance(row, dict):
                raise StaticDatabaseError(f"角色缺少技能配置：{character_id}")
            abilities = row.get("CharacterAbilityList")
            if not isinstance(abilities, list) or not abilities:
                raise StaticDatabaseError(f"角色技能配置为空：{character_id}")
            skill_ids: set[str] = set()
            for entry in abilities:
                if not isinstance(entry, dict):
                    raise StaticDatabaseError(f"角色技能项无效：{character_id}")
                skill_id = entry.get("Key")
                value = entry.get("Value")
                if not isinstance(skill_id, str) or not skill_id or not isinstance(value, dict):
                    raise StaticDatabaseError(f"角色技能身份无效：{character_id}")
                if skill_id in skill_ids:
                    raise StaticDatabaseError(f"角色技能重复：{character_id}/{skill_id}")
                skill_ids.add(skill_id)
                ability_type = enum_tail(value.get("AbilityType"))
                ability_index = value.get("AbilityIndex")
                if ability_type is None or not isinstance(ability_index, int):
                    raise StaticDatabaseError(f"角色技能类型无效：{character_id}/{skill_id}")
                effect = effect_rows.get(skill_id)
                if effect is not None and not isinstance(effect, dict):
                    raise StaticDatabaseError(f"角色技能效果配置无效：{character_id}/{skill_id}")
                tag_data = effect.get("AbilityGameplayTag") if effect else None
                effect_data = effect.get("GameplayEffectToActivate") if effect else None
                gameplay_tag = tag_data.get("TagName") if isinstance(tag_data, dict) else None
                if gameplay_tag is not None and not isinstance(gameplay_tag, str):
                    raise StaticDatabaseError(f"角色技能标签无效：{character_id}/{skill_id}")
                level_rows = value.get("LevelsCostItems")
                if not isinstance(level_rows, list) or not level_rows:
                    raise StaticDatabaseError(f"角色技能等级配置为空：{character_id}/{skill_id}")
                self.connection.execute(
                    "INSERT INTO character_skill VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        character_id,
                        skill_id,
                        ability_type,
                        ability_index,
                        bool_int(value.get("bShowDetailInfo")),
                        gameplay_tag,
                        asset_path(effect_data),
                        bool_int(effect.get("bReliveNeedAddAgain")) if effect else 0,
                        self.source_row_id("character_abilities", row_key),
                        self.source_row_id("character_ability_effects", skill_id) if effect else None,
                    ),
                )
                levels: set[int] = set()
                for level_row in level_rows:
                    if not isinstance(level_row, dict):
                        raise StaticDatabaseError(f"角色技能等级项无效：{character_id}/{skill_id}")
                    level = level_row.get("Level")
                    required_breakthrough = level_row.get("RequireTupoLevel")
                    required_awaken = level_row.get("RequireAwakenLevel")
                    costs = level_row.get("CostItems")
                    if (
                        not isinstance(level, int)
                        or level <= 0
                        or level in levels
                        or not isinstance(required_breakthrough, int)
                        or not 0 <= required_breakthrough <= 6
                        or not isinstance(required_awaken, int)
                        or not 0 <= required_awaken <= 6
                        or not isinstance(costs, list)
                    ):
                        raise StaticDatabaseError(f"角色技能等级数据无效：{character_id}/{skill_id}")
                    levels.add(level)
                    self.connection.execute(
                        "INSERT INTO character_skill_level VALUES (?,?,?,?,?,?)",
                        (
                            character_id,
                            skill_id,
                            level,
                            required_breakthrough,
                            required_awaken,
                            canonical_json(costs),
                        ),
                    )

    def _import_skill_damage(self) -> None:
        """导入官方伤害执行参数，不在此处实现或推导伤害公式。"""

        damage_rows = self.rows["skill_damage"]
        for damage_id in sorted(damage_rows):
            row = damage_rows[damage_id]
            if not isinstance(row, dict):
                raise StaticDatabaseError(f"技能伤害数据无效：{damage_id}")
            ability_id = row.get("GAName")
            if ability_id in ("", "None"):
                ability_id = None
            if ability_id is not None and not isinstance(ability_id, str):
                raise StaticDatabaseError(f"技能伤害技能 ID 无效：{damage_id}")
            rate_arrays = {
                column: row.get(source_key)
                for column, source_key in (
                    ("atk", "AtkRateBaseArray"),
                    ("def", "DefRateBaseArray"),
                    ("hp", "HPRateBaseArray"),
                )
            }
            if any(
                not isinstance(values, list)
                or any(not isinstance(value, (int, float)) for value in values)
                for values in rate_arrays.values()
            ):
                raise StaticDatabaseError(f"技能伤害倍率数组无效：{damage_id}")
            damage_type = enum_tail(row.get("DamageTypeEX"), "DAMAGE_TYPE_")
            source_category = enum_tail(
                row.get("DamageSourceCategory"), "DAMAGE_SOURCE_CATEGORY_"
            )
            attack_break_level = enum_tail(row.get("AttackBreakLevel"), "BL_")
            if not all((damage_type, source_category, attack_break_level)):
                raise StaticDatabaseError(f"技能伤害枚举无效：{damage_id}")
            numeric_fields = (
                "ChargeAdd", "UnbalValue", "HeterochromeAdd", "FixedCritRate",
                "StroyBlanceGERate", "BreakableDamage", "BreakableImpulse",
                "VehicleBreakableImpulse",
            )
            if any(not isinstance(row.get(field), (int, float)) for field in numeric_fields):
                raise StaticDatabaseError(f"技能伤害数值无效：{damage_id}")
            self.connection.execute(
                "INSERT INTO skill_damage VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    damage_id, ability_id, damage_type, float(row["ChargeAdd"]),
                    float(row["UnbalValue"]), float(row["HeterochromeAdd"]),
                    source_category, float(row["FixedCritRate"]),
                    canonical_json(rate_arrays["atk"]), canonical_json(rate_arrays["def"]),
                    canonical_json(rate_arrays["hp"]), float(row["StroyBlanceGERate"]),
                    attack_break_level, bool_int(row.get("bOverrideBreakableDamage")),
                    float(row["BreakableDamage"]),
                    bool_int(row.get("bOverrideBreakableImpulse")),
                    float(row["BreakableImpulse"]),
                    bool_int(row.get("bOverrideVehicleBreakableImpulse")),
                    float(row["VehicleBreakableImpulse"]),
                    self.source_row_id("skill_damage", damage_id),
                ),
            )

        for damage_id in sorted(self.rows["skill_damage_modifiers"]):
            row = self.rows["skill_damage_modifiers"][damage_id]
            coefficient = row.get("FTAtkRateBaseCoefficient") if isinstance(row, dict) else None
            if not isinstance(coefficient, (int, float)):
                raise StaticDatabaseError(f"技能伤害修正数据无效：{damage_id}")
            if damage_id not in damage_rows:
                raise StaticDatabaseError(f"技能伤害修正缺少主记录：{damage_id}")
            self.connection.execute(
                "INSERT INTO skill_damage_modifier VALUES (?,?,?)",
                (damage_id, float(coefficient), self.source_row_id("skill_damage_modifiers", damage_id)),
            )
