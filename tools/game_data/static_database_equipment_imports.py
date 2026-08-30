# 导入装备属性、形状、套装、成长、图纸和默认权重。
"""Static database builder responsibility mixin."""

from __future__ import annotations

from tools.game_data.static_database_build_support import *


class EquipmentImportMixin:
    def _import_equipment_attributes(self) -> None:
        for attribute_id in sorted(self.rows["equipment_attributes"]):
            row = self.rows["equipment_attributes"][attribute_id]
            display, _, _ = text_parts(row.get("AttributeText"))
            filter_data = row.get("AttributeFilterData")
            filter_name, _, _ = text_parts(
                filter_data.get("FilterViewName") if isinstance(filter_data, dict) else None
            )
            random_name, _, _ = text_parts(row.get("EquipmentRandomAttributeName"))
            self.connection.execute(
                "INSERT INTO equipment_attribute VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    attribute_id,
                    display,
                    filter_name,
                    random_name,
                    enum_tail(row.get("AttributeType")),
                    bool_int(row.get("bShowPercent")),
                    bool_int(row.get("bShowOutside")),
                    bool_int(row.get("bShowInner")),
                    row.get("Score"),
                    asset_path(row.get("AttributeIcon")),
                    self.source_row_id("equipment_attributes", attribute_id),
                ),
            )

    def _import_official_character_shape_bonuses(self) -> None:
        """Import per-drive extra-shape bonuses from the official slot relation."""

        attributes = {
            str(row[0]): bool(row[1])
            for row in self.connection.execute(
                "SELECT attribute_id, show_percent FROM equipment_attribute"
            )
        }
        rules: dict[str, tuple[int, int, tuple[tuple[str, float], ...]]] = {}
        for raw_character_id in sorted(self.rows["character"], key=int):
            character_row = self.rows["character"][raw_character_id]
            element = character_row.get("ElementData")
            if not isinstance(element, dict):
                continue
            slot_id = optional_text(element.get("EquipmentSlotID"))
            if slot_id is None or slot_id == "None":
                continue
            slot_row = self.rows["character_equipment_slots"].get(slot_id)
            if not isinstance(slot_row, dict):
                raise StaticDatabaseError(
                    f"角色 {raw_character_id} 的额外形状配置不存在：{slot_id}"
                )
            modify_pack_id = optional_text(slot_row.get("ModifyPropID"))
            modify_row = self.rows["equipment_slot_modify"].get(modify_pack_id or "")
            if not isinstance(modify_row, dict):
                raise StaticDatabaseError(
                    f"角色 {raw_character_id} 的额外形状修正不存在：{modify_pack_id}"
                )
            conditions = modify_row.get("ConditionArray") or ()
            if any(value not in ({}, None) for value in conditions):
                raise StaticDatabaseError(
                    f"角色 {raw_character_id} 的额外形状包含未建模条件：{modify_pack_id}"
                )
            grid_count = int(slot_row.get("OwnerGridCount") or 0)
            if grid_count <= 0:
                raise StaticDatabaseError(
                    f"角色 {raw_character_id} 的额外形状格数无效：{slot_id}"
                )
            properties: list[tuple[str, float]] = []
            for modifier in modify_row.get("ModifyData") or ():
                if not isinstance(modifier, dict):
                    raise StaticDatabaseError(
                        f"角色 {raw_character_id} 的额外形状修正格式无效：{modify_pack_id}"
                    )
                property_id = str(modifier.get("PropName") or "")
                if property_id not in attributes:
                    raise StaticDatabaseError(
                        f"角色 {raw_character_id} 的额外形状属性不存在：{property_id}"
                    )
                operation = enum_tail(modifier.get("ModifierOp"))
                if operation != "MODIFY_MODOP_ADDITIVE":
                    raise StaticDatabaseError(
                        f"角色 {raw_character_id} 的额外形状不是加法修正：{modify_pack_id}"
                    )
                raw_value = float(modifier.get("PropValue") or 0.0)
                display_value = raw_value * 100.0 if attributes[property_id] else raw_value
                if any(existing_id == property_id for existing_id, _ in properties):
                    raise StaticDatabaseError(
                        f"角色 {raw_character_id} 的额外形状属性重复：{property_id}"
                    )
                properties.append((property_id, display_value))
            if not properties:
                raise StaticDatabaseError(
                    f"角色 {raw_character_id} 的额外形状没有属性：{modify_pack_id}"
                )
            annotation = self.connection.execute(
                """SELECT logical_character_key
                   FROM character_annotation WHERE character_id = ?""",
                (int(raw_character_id),),
            ).fetchone()
            if annotation is None:
                continue
            logical_key = str(annotation[0])
            candidate = (int(raw_character_id), grid_count, tuple(properties))
            previous = rules.get(logical_key)
            if previous is not None and previous[1:] != candidate[1:]:
                raise StaticDatabaseError(
                    f"逻辑角色 {logical_key} 存在冲突的官方额外形状配置"
                )
            if previous is None or candidate[0] < previous[0]:
                rules[logical_key] = candidate

        for logical_key in sorted(rules):
            representative_id, grid_count, properties = rules[logical_key]
            self.connection.execute(
                """INSERT INTO logical_character_shape_bonus(
                       logical_character_key, representative_character_id,
                       shape_label, shape_grid_count, source_kind
                   ) VALUES (?, ?, ?, ?, 'official_role_profile')""",
                (logical_key, representative_id, f"Type-{grid_count}", grid_count),
            )
            self.connection.executemany(
                """INSERT INTO logical_character_shape_bonus_property(
                       logical_character_key, property_id, display_value, ordinal
                   ) VALUES (?, ?, ?, ?)""",
                [
                    (logical_key, property_id, display_value, ordinal)
                    for ordinal, (property_id, display_value) in enumerate(properties)
                ],
            )

    def _import_equipment_shapes(self) -> None:
        for shape_id in sorted(self.rows["equipment_shapes"]):
            row = self.rows["equipment_shapes"][shape_id]
            cells = row.get("Shape")
            delta = row.get("FirstGridDeltaPos")
            if not isinstance(cells, list) or not isinstance(delta, dict):
                raise StaticDatabaseError(f"驱动形状无效：{shape_id}")
            self.connection.execute(
                "INSERT INTO equipment_shape VALUES (?,?,?,?,?)",
                (
                    shape_id,
                    len(cells),
                    int(delta["X"]),
                    int(delta["Y"]),
                    self.source_row_id("equipment_shapes", shape_id),
                ),
            )
            for ordinal, cell in enumerate(cells):
                self.connection.execute(
                    "INSERT INTO equipment_shape_cell VALUES (?,?,?,?)",
                    (shape_id, ordinal, int(cell["X"]), int(cell["Y"])),
                )

    def _import_equipment_suits(self) -> None:
        for suit_id in sorted(self.rows["equipment_suits"]):
            row = self.rows["equipment_suits"][suit_id]
            name, text_table, text_key = text_parts(row.get("SuitTitle"))
            if name is None:
                raise StaticDatabaseError(f"空幕套装没有名称：{suit_id}")
            self.connection.execute(
                "INSERT INTO equipment_suit VALUES (?,?,?,?,?,?)",
                (
                    suit_id,
                    name,
                    text_table,
                    text_key,
                    asset_path(row.get("SuitIcon")),
                    self.source_row_id("equipment_suits", suit_id),
                ),
            )
            shapes = row.get("SuitGeometryCondition")
            if not isinstance(shapes, list):
                raise StaticDatabaseError(f"空幕套装缺少形状列表：{suit_id}")
            for ordinal, shape_id in enumerate(shapes):
                self.connection.execute(
                    "INSERT INTO equipment_suit_required_shape VALUES (?,?,?)",
                    (suit_id, ordinal, shape_id),
                )
            effects = row.get("SuitStructList")
            if not isinstance(effects, list):
                raise StaticDatabaseError(f"空幕套装缺少效果列表：{suit_id}")
            for effect in effects:
                description, description_table, description_key = text_parts(
                    effect.get("SuitBuffDescription")
                )
                buff = effect.get("SuitBuff")
                self.connection.execute(
                    "INSERT INTO equipment_suit_effect VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        suit_id,
                        int(effect["SuitCondition"]),
                        effect.get("SuitModifyPackID"),
                        buff.get("ObjectPath") if isinstance(buff, dict) else None,
                        description,
                        description_table,
                        description_key,
                        bool_int(effect.get("bReliveNeedAddAgain")),
                        self.source_row_id("equipment_suits", suit_id),
                    ),
                )
    def _import_equipment_items(self) -> None:
        for item_id in sorted(self.rows["equipment"]):
            row = self.rows["equipment"][item_id]
            element = row.get("ElementData")
            if not isinstance(element, dict):
                raise StaticDatabaseError(f"装备缺少 ElementData：{item_id}")
            is_core = bool(element.get("IsCore"))
            name, text_table, text_key = text_parts(row.get("ItemName"))
            geometry_enum = element.get("EquipmentGeometryType")
            geometry = enum_tail(geometry_enum, "EquipmentGeometry_")
            geometry_id = (
                None if is_core or geometry == "Core" else f"EquipmentGeometry_{geometry}"
            )
            suit_id = element.get("SuitPackID") if is_core else None
            if is_core and suit_id not in self.rows["equipment_suits"]:
                raise StaticDatabaseError(
                    f"核心引用了未知的官方 SuitPackID：{item_id}/{suit_id}"
                )
            quality = enum_tail(row.get("ItemQuality"), "ITEM_QUALITY_")
            if quality is None or name is None:
                raise StaticDatabaseError(f"装备身份字段不完整：{item_id}")
            self.connection.execute(
                "INSERT INTO equipment_item VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    item_id,
                    "core" if is_core else "module",
                    quality,
                    name,
                    text_table,
                    text_key,
                    geometry_id,
                    geometry_enum,
                    optional_int(element.get("OwnGridNum")),
                    suit_id,
                    element.get("EquipmentSuitType"),
                    int(element.get("MaxStrengthenLevel", 0)),
                    element.get("RandomBaseAttributeId"),
                    int(element.get("RandomBaseCount", 0)),
                    element.get("RandomAttributeId"),
                    int(element.get("RandomAttributeCount", 0)),
                    int(element.get("RandomAttributeMaxCount", 0)),
                    element.get("StrengthPackId"),
                    asset_path(row.get("ItemIcon")),
                    asset_path(element.get("PlanIcon")),
                    bool_int("_guide_" in item_id),
                    self.source_row_id("equipment", item_id),
                ),
            )

    def _import_equipment_progression(self) -> None:
        for row_key in sorted(self.rows["equipment_strength"]):
            pack_id, level = split_numbered_row(row_key)
            row = self.rows["equipment_strength"][row_key]
            self.connection.execute(
                "INSERT INTO equipment_strength_level VALUES (?,?,?,?)",
                (pack_id, level, int(row["NeedExp"]), self.source_row_id("equipment_strength", row_key)),
            )
        for curve_id in sorted(self.rows["equipment_curves"]):
            row = self.rows["equipment_curves"][curve_id]
            self.connection.execute(
                "INSERT INTO equipment_base_attribute_curve VALUES (?,?,?,?,?,?)",
                (
                    curve_id,
                    enum_tail(row.get("InterpMode")),
                    enum_tail(row.get("PreInfinityExtrap")),
                    enum_tail(row.get("PostInfinityExtrap")),
                    row.get("DefaultValue"),
                    self.source_row_id("equipment_curves", curve_id),
                ),
            )
            for ordinal, point in enumerate(row.get("Keys", [])):
                self.connection.execute(
                    "INSERT INTO equipment_base_attribute_point VALUES (?,?,?,?)",
                    (curve_id, ordinal, float(point["Time"]), float(point["Value"])),
                )
        for attribute_id in sorted(self.rows["equipment_core_random"]):
            row = self.rows["equipment_core_random"][attribute_id]
            content, table, key = text_parts(row.get("Content"))
            self.connection.execute(
                "INSERT INTO equipment_core_random_attribute VALUES (?,?,?,?,?)",
                (
                    attribute_id,
                    content,
                    table,
                    key,
                    self.source_row_id("equipment_core_random", attribute_id),
                ),
            )

    def _import_equipment_plans(self) -> None:
        for character_id in sorted(self.rows["equipment_plans"], key=int):
            row = self.rows["equipment_plans"][character_id]
            self.connection.execute(
                "INSERT INTO equipment_plan VALUES (?,?,?,?,?,?,?,?)",
                (
                    int(character_id),
                    row["CoreID"],
                    int(row["CoreLvl"]),
                    int(row["EquipmentLvl"]),
                    float(row["ReferScore"]),
                    asset_path(row.get("EquipPlanBg")),
                    asset_path(row.get("CharacterTabImg")),
                    self.source_row_id("equipment_plans", character_id),
                ),
            )
            for table, values in (
                ("equipment_plan_core_attribute", row.get("CoreMainAttrList", [])),
                ("equipment_plan_recommended_attribute", row.get("RecommendAttrList", [])),
            ):
                for ordinal, attribute_id in enumerate(values):
                    self.connection.execute(
                        f"INSERT INTO {table} VALUES (?,?,?)",
                        (int(character_id), ordinal, attribute_id),
                    )
            cells, _ = parse_plan_grid(row.get("EquipmentSlots"))
            for board_row, column, anchor in cells:
                self.connection.execute(
                    "INSERT INTO equipment_plan_cell VALUES (?,?,?,?)",
                    (int(character_id), board_row, column, anchor),
                )
            for ordinal, item_id in enumerate(row.get("EquipmentList", [])):
                self.connection.execute(
                    "INSERT INTO equipment_plan_module VALUES (?,?,?)",
                    (int(character_id), ordinal, item_id),
                )

    def _import_default_character_weights(self) -> None:
        """Seed role-aware fallbacks; the developer API replaces available rows.

        The official plan provides an ordered recommendation, not numeric weights.
        Keep that order, reuse only established percentage-stat weights and leave
        incomparable flat stats out of the score fallback.
        """

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        override_path = (
            PROJECT_ROOT / "tools" / "game_data" / "character_weight_overrides.json"
        )
        overrides = json.loads(override_path.read_text(encoding="utf-8"))
        if not isinstance(overrides, dict):
            raise StaticDatabaseError("角色推荐权重覆盖必须是对象")
        established_weights = dict(DEFAULT_RECOMMENDED_WEIGHTS)
        elemental_weight = established_weights["DamageUpGeneralBase"]
        character_ids = [
            int(row[0])
            for row in self.connection.execute(
                "SELECT character_id FROM equipment_plan ORDER BY character_id"
            )
        ]
        for character_id in character_ids:
            override = overrides.get(str(character_id))
            ordered_properties = [
                str(row[0])
                for row in self.connection.execute(
                    """
                    SELECT attribute_id
                    FROM equipment_plan_recommended_attribute
                    WHERE character_id = ?
                    ORDER BY ordinal
                    """,
                    (character_id,),
                )
            ]
            if override is not None:
                if not isinstance(override, dict):
                    raise StaticDatabaseError(f"角色 {character_id} 推荐权重覆盖无效")
                fallback_rows = [
                    (str(row["property_id"]), float(row["weight"]))
                    for row in override.get("properties") or ()
                ]
                if not fallback_rows or len({row[0] for row in fallback_rows}) != len(
                    fallback_rows
                ):
                    raise StaticDatabaseError(f"角色 {character_id} 推荐权重覆盖为空或重复")
                source_name = str(
                    override.get("source_name")
                    or "preimplementation_workshop_fallback"
                )
            else:
                fallback_rows = []
                for property_id in ordered_properties:
                    weight = established_weights.get(property_id)
                    if (
                        weight is None
                        and property_id.startswith("DamageUp")
                        and property_id.endswith("Base")
                    ):
                        weight = elemental_weight
                    if weight is not None and property_id not in {
                        item[0] for item in fallback_rows
                    }:
                        fallback_rows.append((property_id, float(weight)))
                for property_id, weight in DEFAULT_RECOMMENDED_WEIGHTS:
                    if property_id not in {item[0] for item in fallback_rows}:
                        fallback_rows.append((property_id, float(weight)))
                source_name = "official_plan_fallback"
            self.connection.execute(
                """
                INSERT INTO character_weight_recommendation
                VALUES (?, 'default', NULL, ?, ?)
                """,
                (character_id, source_name, now),
            )
            self.connection.executemany(
                """INSERT INTO character_weight_recommendation_property(
                       character_id, property_id, weight, main_weight, ordinal
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (character_id, property_id, weight, weight, ordinal)
                    for ordinal, (property_id, weight) in enumerate(fallback_rows)
                ],
            )
