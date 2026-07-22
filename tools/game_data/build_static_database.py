# 从准备好的游戏官方文件目录构建版本化静态 SQLite 数据库。
"""从准备好的 Content 数据目录构建版本化 NTE 静态 SQLite 数据库。

游戏官方文件和中间数据始终保存在项目外。本工具读取已有数据目录，镜像所需来源
记录，标准化角色、装备和弧盘数据，并生成审计报告；不会改变应用当前基于 JSON 的
运行逻辑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .catalog_characters import (
        DEFAULT_OVERRIDES,
        build_catalog as build_character_catalog,
        load_datatable,
        resolve_content_root,
    )
except ImportError:  # 支持直接运行：python tools/game_data/build_static_database.py
    from catalog_characters import (  # type: ignore[no-redef]
        DEFAULT_OVERRIDES,
        build_catalog as build_character_catalog,
        load_datatable,
        resolve_content_root,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema"
SCHEMA_PATHS = (
    SCHEMA_DIR / "002_game_static.sql",
    SCHEMA_DIR / "003_game_static_combat.sql",
    SCHEMA_DIR / "004_game_static_monster_binding.sql",
    SCHEMA_DIR / "005_game_static_abyss_binding.sql",
)
IMPORTER_VERSION = 5

TABLE_PATHS = {
    "character": "DataTable/Character/DT_Character.json",
    "character_abilities": "DataTable/Character/DT_CharacterAbilityConfig.json",
    "player_pack": "DataTable/PackData/DT_PlayerPackData.json",
    "equipment": "DataTable/Equipment/DT_Equipment.json",
    "equipment_attributes": "DataTable/PackData/ModifyData/DT_AttributeStaticData.json",
    "equipment_shapes": "DataTable/Equipment/DT_EquipmentShapeFeatureData.json",
    "equipment_suits": "DataTable/Equipment/DT_EquipmentSuitData.json",
    "equipment_plans": "DataTable/Equipment/DT_EquipmentPlanData.json",
    "equipment_strength": "DataTable/Equipment/DT_EquipmentStrengthData.json",
    "equipment_curves": "DataTable/Equipment/CT_EquipmentBaseAttribute.json",
    "equipment_core_random": "DataTable/Equipment/DT_EquipmnetCoreRandomAttributeData.json",
    "fork_types": "DataTable/Fork/DT_ForkTypeData.json",
    "fork_items": "DataTable/Fork/DT_ForkItemData.json",
    "fork_upgrades": "DataTable/Fork/DT_ForkUpgradeData.json",
    "fork_stars": "DataTable/Fork/DT_ForkUpgradeStarDataTable.json",
    "fork_breakthroughs": "DataTable/Fork/DT_ForkBreakthroughData.json",
    "fork_modify": "DataTable/PackData/ModifyData/DT_ForkModifyData.json",
    "combat_global_curves": "DataTable/skill/GlobalCharacterData/DT_GlobalCommonData.json",
    "reaction_damage": "DataTable/Reaction/DT_ReactionDamageData.json",
    "reaction_definitions": "DataTable/Reaction/DT_ReactionData.json",
    "reaction_constants": "DataTable/Reaction/DT_ReactionEffectFigure.json",
    "skill_damage": "DataTable/skill/DT_SkillDamageData.json",
    "monster_pack": "DataTable/PackData/DT_MonsterPackData.json",
    "monster_pack_night_999": "DataTable/PackData/DT_MonsterPackData_FT.json",
    "monster_static_big_world": "DataTable/Monster/DT_MonsterStaticData_BigWorld.json",
    "monster_static_big_world_gameplay": "DataTable/Monster/DT_MonsterStaticData_BigWorld_Gameplay.json",
    "monster_static_big_world_quest": "DataTable/Monster/DT_MonsterStaticData_BigWorld_Quest.json",
    "monster_static_clone": "DataTable/Monster/DT_MonsterStaticData_Clone.json",
    "monster_static_abyss": "DataTable/Monster/DT_MonsterStaticData_Abyss.json",
    "abyss_clone_levels": "DataAssets/DataAssetSet/Abyss/AbyssCloneLevelDataTable.json",
    "abyss_monster_pools": "DataAssets/DataAssetSet/Abyss/DT_AbyssMonsterPool.json",
}

REACTION_CONSTANT_METADATA = {
    "LingZhouCopyCoef": ("ratio", "覆纹追加伤害的基础比例"),
    "Reaction_GuangLingXiang_Charge": ("points", "盈蓄提供的额外终结能量"),
    "Reaction_GuangLingXiang_ChargeCD": ("seconds", "盈蓄获得能量的触发间隔"),
    "Reaction_ZhouAn_BuffTime": ("seconds", "浊燃持续时间"),
    "Reaction_ZhouAn_Period": ("seconds", "浊燃伤害周期"),
    "Reaction_ZhouAn8_DotDamageUP_1003": ("ratio", "早雾天赋每种持续伤害状态的增伤"),
    "Reaction_ZhouAn8_LimitDotDamageUP_1003": ("ratio", "早雾天赋持续伤害增伤上限"),
    "Reaction_HunXiang_BuffTime": ("seconds", "浸染持续时间"),
    "Reaction_HunXiang_DamageUP": ("ratio", "浸染魂/相伤害基础提升"),
    "Reaction_AnHun_BuffTime": ("seconds", "黯星基础持续时间"),
    "Reaction_GuangXiang_BuffTime": ("seconds", "延滞基础持续时间"),
    "Reaction_LingZhou_BuffTime": ("seconds", "覆纹基础持续时间"),
}

ENEMY_RESISTANCE_FIELDS = {
    "normal": ("DamageResistNormalBase", "DamageImmuNormal"),
    "cosmos": ("DamageResistCosmosBase", "DamageImmuCosmos"),
    "nature": ("DamageResistNatureBase", "DamageImmuNature"),
    "incantation": ("DamageResistIncantationBase", "DamageImmuIncantation"),
    "chaos": ("DamageResistChaosBase", "DamageImmuChaos"),
    "psyche": ("DamageResistPsycheBase", "DamageImmuPsyche"),
    "lakshana": ("DamageResistLakshanaBase", "DamageImmuLakshana"),
    "psychically": ("DamageResistPsychicallyBase", "DamageImmuPsychically"),
}

FORK_TYPE_ID_BY_CHARACTER_GROUP = {
    "ECharacterGroupType::CHARACTER_GROUP_TYPE_ONE": 1,
    "ECharacterGroupType::CHARACTER_GROUP_TYPE_TWO": 2,
    "ECharacterGroupType::CHARACTER_GROUP_TYPE_THREE": 3,
    "ECharacterGroupType::CHARACTER_GROUP_TYPE_FOUR": 4,
    "ECharacterGroupType::CHARACTER_GROUP_TYPE_FIVE": 5,
}

class StaticDatabaseError(RuntimeError):
    """必要的来源数据关系无法标准化。"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_parts(value: Any) -> tuple[str | None, str | None, str | None]:
    if not isinstance(value, dict):
        return None, None, None
    text = value.get("LocalizedString") or value.get("SourceString")
    return (
        text if isinstance(text, str) and text else None,
        value.get("TableId") if isinstance(value.get("TableId"), str) else None,
        value.get("Key") if isinstance(value.get("Key"), str) else None,
    )


def asset_path(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    path = value.get("AssetPathName")
    return path if isinstance(path, str) and path else None


def bool_int(value: Any) -> int:
    return int(bool(value))


def optional_int(value: Any) -> int | None:
    if value in (None, "", "None"):
        return None
    return int(value)


def optional_text(value: Any) -> str | None:
    if not isinstance(value, str) or value in ("", "None"):
        return None
    return value


def float_value(row: dict[str, Any], key: str, *, default: float = 0.0) -> float:
    value = row.get(key, default)
    if not isinstance(value, (int, float)):
        raise StaticDatabaseError(f"字段 {key} 不是数值：{value!r}")
    return float(value)


def enum_tail(value: Any, prefix: str = "") -> str | None:
    if not isinstance(value, str) or not value:
        return None
    tail = value.rsplit("::", 1)[-1]
    return tail.removeprefix(prefix)


def split_numbered_row(row_key: str) -> tuple[str, int]:
    match = re.fullmatch(r"(.+)_([0-9]+)", row_key)
    if match is None:
        raise StaticDatabaseError(f"记录键应以数字结尾：{row_key}")
    return match.group(1), int(match.group(2))


def parse_plan_grid(rows: Any) -> tuple[list[tuple[int, int, str | None]], list[tuple[int, int, str]]]:
    if not isinstance(rows, list) or len(rows) != 7:
        raise StaticDatabaseError("官方装配网格必须包含七行")
    cells: list[tuple[int, int, str | None]] = []
    anchors: list[tuple[int, int, str]] = []
    for source_row, encoded in enumerate(rows):
        if not isinstance(encoded, str):
            raise StaticDatabaseError("官方装配网格行必须是逗号分隔字符串")
        values = encoded.split(",")
        if len(values) != 7:
            raise StaticDatabaseError("官方装配网格行必须包含七列")
        for source_column, value in enumerate(values):
            if value == "-1":
                continue
            if not 1 <= source_row <= 5 or not 1 <= source_column <= 5:
                raise StaticDatabaseError("可用装配格超出了 5×5 底盘")
            anchor = None if value == "0" else value
            cells.append((source_row, source_column, anchor))
            if anchor is not None:
                anchors.append((source_row, source_column, anchor))
    return cells, anchors


def _show_time(row: dict[str, Any]) -> str | None:
    element = row.get("ElementData")
    if not isinstance(element, dict) or not element.get("bCheckShowTime"):
        return None
    show_time = element.get("ShowTime")
    mainland = show_time.get("MainlandTime") if isinstance(show_time, dict) else None
    if not isinstance(mainland, dict):
        return None
    try:
        return datetime(
            int(mainland["Year"]),
            int(mainland["Month"]),
            int(mainland["Day"]),
            int(mainland.get("Hour", 0)),
            int(mainland.get("minute", 0)),
            int(mainland.get("Second", 0)),
        ).isoformat(timespec="seconds")
    except (KeyError, TypeError, ValueError):
        return None


class StaticDatabaseBuilder:
    def __init__(
        self,
        connection: sqlite3.Connection,
        content_root: Path,
        *,
        dataset_id: str,
        game_version: str | None,
        as_of: date,
        overrides_path: Path,
        include_source_payloads: bool = True,
    ) -> None:
        self.connection = connection
        self.content_root = content_root
        self.dataset_id = dataset_id
        self.game_version = game_version
        self.as_of = as_of
        self.overrides_path = overrides_path
        self.include_source_payloads = include_source_payloads
        self.rows: dict[str, dict[str, Any]] = {}
        self.source_row_ids: dict[tuple[str, str], int] = {}

    def build(self) -> dict[str, Any]:
        for schema_path in SCHEMA_PATHS:
            self.connection.executescript(schema_path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.connection.executemany(
            "INSERT INTO schema_migration VALUES (?, ?)",
            ((version, now) for version in range(2, IMPORTER_VERSION + 1)),
        )
        self.connection.execute(
            "INSERT INTO dataset VALUES (?, ?, ?, ?)",
            (self.dataset_id, self.game_version, IMPORTER_VERSION, now),
        )
        self._mirror_sources()
        self._import_characters()
        self._import_equipment_attributes()
        self._import_equipment_shapes()
        self._import_equipment_suits()
        self._import_equipment_items()
        self._import_equipment_progression()
        self._import_equipment_plans()
        self._import_forks()
        self._import_combat_level_curves()
        self._import_reactions()
        self._import_skill_damage()
        self._import_enemy_combat_profiles()
        self._import_monster_instance_profiles()
        self._import_abyss_bindings()
        violations = [tuple(row) for row in self.connection.execute("PRAGMA foreign_key_check")]
        if violations:
            raise StaticDatabaseError(f"发现外键错误：{violations[:10]}")
        self.connection.commit()
        return self._database_counts()

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
                    _show_time(row),
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

    def _import_forks(self) -> None:
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
                    element.get("BreakthroughPackId"),
                    element.get("UpgradeStarPackID"),
                    element.get("MaxBreakthrough"),
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

    def _import_combat_level_curves(self) -> None:
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
                "topple:character_level",
                "topple",
                None,
                None,
                enum_tail(topple_row.get("InterpMode")),
                "exact_level",
                self.source_row_id("combat_global_curves", topple_row_id),
            ),
        )
        for ordinal, point in enumerate(topple_points):
            self.connection.execute(
                "INSERT INTO combat_level_curve_point VALUES (?,?,?,?,?)",
                (
                    "topple:character_level",
                    ordinal,
                    float(point["Time"]),
                    None,
                    float(point["Value"]),
                ),
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
                    curve_id,
                    "reaction",
                    reaction_type,
                    effect_id,
                    None,
                    "source_tier",
                    self.source_row_id("reaction_damage", effect_id),
                ),
            )
            for source_tier, value in enumerate(values):
                self.connection.execute(
                    "INSERT INTO combat_level_curve_point VALUES (?,?,?,?,?)",
                    (curve_id, source_tier, None, source_tier, float(value)),
                )

    def _import_reactions(self) -> None:
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
                    official_type,
                    element_type_1,
                    element_type_2,
                    optional_text(row.get("DefaultDamageGE")),
                    self.source_row_id("reaction_definitions", reaction_type),
                ),
            )

        for constant_id in sorted(self.rows["reaction_constants"]):
            row = self.rows["reaction_constants"][constant_id]
            keys = row.get("Keys")
            if not isinstance(keys, list) or len(keys) != 1:
                raise StaticDatabaseError(
                    f"环合常量必须恰好包含一个官方曲线点：{constant_id}"
                )
            unit, description = REACTION_CONSTANT_METADATA.get(
                constant_id, ("scalar", None)
            )
            point = keys[0]
            self.connection.execute(
                "INSERT INTO combat_effect_constant VALUES (?,?,?,?,?,?)",
                (
                    constant_id,
                    float(point["Time"]),
                    float(point["Value"]),
                    unit,
                    description,
                    self.source_row_id("reaction_constants", constant_id),
                ),
            )

    def _import_skill_damage(self) -> None:
        rate_fields = {
            "attack": "AtkRateBaseArray",
            "health": "HPRateBaseArray",
            "defense": "DefRateBaseArray",
        }
        for effect_id in sorted(self.rows["skill_damage"]):
            row = self.rows["skill_damage"][effect_id]
            damage_type = enum_tail(row.get("DamageTypeEX"))
            if damage_type is None:
                raise StaticDatabaseError(f"技能伤害缺少 DamageTypeEX：{effect_id}")
            self.connection.execute(
                "INSERT INTO skill_damage VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    effect_id,
                    optional_text(row.get("GAName")),
                    damage_type,
                    enum_tail(row.get("DamageSourceCategory")),
                    float_value(row, "FixedCritRate"),
                    float_value(row, "ChargeAdd"),
                    float_value(row, "UnbalValue"),
                    float_value(row, "HeterochromeAdd"),
                    float_value(row, "StroyBlanceGERate", default=1.0),
                    enum_tail(row.get("AttackBreakLevel")),
                    self.source_row_id("skill_damage", effect_id),
                ),
            )
            for scaling_stat, field_name in rate_fields.items():
                values = row.get(field_name, [])
                if not isinstance(values, list):
                    raise StaticDatabaseError(
                        f"技能倍率字段不是数组：{effect_id}/{field_name}"
                    )
                for source_tier, value in enumerate(values):
                    self.connection.execute(
                        "INSERT INTO skill_damage_rate VALUES (?,?,?,?)",
                        (effect_id, scaling_stat, source_tier, float(value)),
                    )

    def _import_enemy_combat_profiles(self) -> None:
        for table_name, profile_set in (
            ("monster_pack", "standard"),
            ("monster_pack_night_999", "night_999"),
        ):
            for pack_id in sorted(self.rows[table_name]):
                row = self.rows[table_name][pack_id]
                self.connection.execute(
                    "INSERT INTO enemy_combat_profile VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        profile_set,
                        pack_id,
                        float_value(row, "DefBase"),
                        float_value(row, "DefUp"),
                        float_value(row, "DefAdd"),
                        float_value(row, "DefIgnore"),
                        float_value(row, "UnbalMax"),
                        float_value(row, "UnbalAccrueEfficiencyBase"),
                        float_value(row, "UnbalAntiAccrueEfficiencyBase"),
                        float_value(row, "UnbaleBonus"),
                        float_value(row, "UnbalReduceNatur"),
                        float_value(row, "UnbalReduceReset"),
                        self.source_row_id(table_name, pack_id),
                    ),
                )
                for damage_type, (resistance_field, immunity_field) in (
                    ENEMY_RESISTANCE_FIELDS.items()
                ):
                    self.connection.execute(
                        "INSERT INTO enemy_element_resistance VALUES (?,?,?,?,?)",
                        (
                            profile_set,
                            pack_id,
                            damage_type,
                            float_value(row, resistance_field),
                            float_value(row, immunity_field),
                        ),
                    )

    def _import_monster_instance_profiles(self) -> None:
        """Import static monster IDs and their explicit standard-pack references.

        ``FT_`` actor names identify 999 夜 content, not an Abyss scene. This
        importer preserves only explicit pack IDs in the static tables; Abyss
        scene binding is imported only from its dedicated scene sources.
        """
        variant_fields = (
            ("world_level", "WorldLevelArray", "MonsterWorldLevel", "MonsterWorldLevelPropModifyID"),
            ("clone_level", "CloneDifficultyLevelArray", "MonsterCloneLevel", "MonsterClonePropModifyID"),
            ("abyss_level", "AbyssCloneLevelArray", "MonsterAbyssLevel", "MonsterAbyssPropModifyID"),
        )
        for table_name in sorted(name for name in self.rows if name.startswith("monster_static_")):
            for monster_id in sorted(self.rows[table_name]):
                row = self.rows[table_name][monster_id]
                default_pack_id = optional_text(row.get("PropModifyID"))
                self.connection.execute(
                    "INSERT INTO monster_instance_profile VALUES (?,?,?,?,?,?,?)",
                    (
                        table_name,
                        monster_id,
                        optional_int(row.get("MonsterLevel")) or 0,
                        "standard",
                        default_pack_id,
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
                        if pack_id is None or level is None:
                            continue
                        self.connection.execute(
                            "INSERT OR IGNORE INTO monster_instance_profile_variant VALUES (?,?,?,?,?,?)",
                            (table_name, monster_id, variant_kind, level, "standard", pack_id),
                        )

    def _import_abyss_bindings(self) -> None:
        """Import the explicit Abyss level → pool → monster → attribute chain."""
        level_table = self.rows["abyss_clone_levels"]
        pool_table = self.rows["abyss_monster_pools"]
        profile_rows = self.rows["monster_pack"]

        for level_config_id in sorted(level_table):
            source_row_id = self.source_row_id("abyss_clone_levels", level_config_id)
            level_config = level_table[level_config_id]
            levels = level_config.get("LevelConfigArray", [])
            if not isinstance(levels, list):
                raise StaticDatabaseError(
                    f"Abyss 关卡配置不是数组：{level_config_id}/LevelConfigArray"
                )
            for level in levels:
                if not isinstance(level, dict):
                    raise StaticDatabaseError(f"Abyss 关卡配置不是对象：{level_config_id}")
                level_id = optional_int(level.get("LevelID"))
                if level_id is None:
                    raise StaticDatabaseError(f"Abyss 关卡缺少 LevelID：{level_config_id}")
                name_zh, _, _ = text_parts(level.get("LevelName"))
                self.connection.execute(
                    "INSERT INTO abyss_level VALUES (?,?,?,?,?)",
                    (
                        level_config_id,
                        level_id,
                        optional_text(level.get("AbyssID")),
                        name_zh,
                        source_row_id,
                    ),
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

        for monster_pool_id in sorted(pool_table):
            pool_source_row_id = self.source_row_id("abyss_monster_pools", monster_pool_id)
            monsters = pool_table[monster_pool_id].get("MonsterPoolArray", [])
            if not isinstance(monsters, list):
                raise StaticDatabaseError(f"Abyss 怪物池不是数组：{monster_pool_id}")
            for ordinal, monster in enumerate(monsters):
                if not isinstance(monster, dict):
                    raise StaticDatabaseError(f"Abyss 怪物池条目不是对象：{monster_pool_id}")
                attribute_pack_id = optional_text(monster.get("AttributeID"))
                if attribute_pack_id is None or attribute_pack_id not in profile_rows:
                    raise StaticDatabaseError(
                        f"Abyss 属性包未在 DT_MonsterPackData 中找到：{monster_pool_id}/{attribute_pack_id}"
                    )
                monster_level = optional_int(monster.get("MonsterLevel"))
                monster_count = optional_int(monster.get("MonsterCount"))
                if monster_level is None or monster_count is None:
                    raise StaticDatabaseError(f"Abyss 怪物缺少等级或数量：{monster_pool_id}")
                self.connection.execute(
                    "INSERT INTO abyss_monster_pool_entry VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        monster_pool_id, ordinal, asset_path(monster.get("MonsterClass")),
                        monster_count, monster_level, "standard", attribute_pack_id,
                        pool_source_row_id,
                        self.source_row_id("monster_pack", attribute_pack_id),
                    ),
                )

    def _database_counts(self) -> dict[str, int]:
        tables = (
            "source_file",
            "source_row",
            "character",
            "character_annotation",
            "equipment_attribute",
            "equipment_shape",
            "equipment_shape_cell",
            "equipment_suit",
            "equipment_suit_effect",
            "equipment_item",
            "equipment_plan",
            "fork_type",
            "fork_item",
            "fork_upgrade_level",
            "fork_modify_pack",
            "fork_breakthrough",
            "fork_star_level",
            "combat_level_curve",
            "combat_level_curve_point",
            "reaction_definition",
            "combat_effect_constant",
            "skill_damage",
            "skill_damage_rate",
            "enemy_combat_profile",
            "enemy_element_resistance",
            "monster_instance_profile",
            "monster_instance_profile_variant",
            "abyss_level",
            "abyss_level_monster_spawn",
            "abyss_monster_pool_entry",
        )
        return {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }


def render_report(report: dict[str, Any]) -> str:
    counts = report["database_counts"]
    lines = [
        "# NTE 静态数据库构建报告",
        "",
        f"数据集：`{report['dataset_id']}`；构建时间：`{report['built_at_utc']}`。",
        "",
        "## 数据库数量",
        "",
    ]
    lines.extend(f"- `{table}`：{count}" for table, count in counts.items())
    return "\n".join(lines)


def build_database(
    source: Path,
    output: Path,
    report_dir: Path,
    *,
    dataset_id: str,
    game_version: str | None,
    as_of: date,
    overrides_path: Path = DEFAULT_OVERRIDES,
    include_source_payloads: bool = True,
) -> dict[str, Any]:
    content_root = resolve_content_root(source)
    output = output.expanduser().resolve()
    report_dir = report_dir.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        connection = sqlite3.connect(temporary)
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            builder = StaticDatabaseBuilder(
                connection,
                content_root,
                dataset_id=dataset_id,
                game_version=game_version,
                as_of=as_of,
                overrides_path=overrides_path,
                include_source_payloads=include_source_payloads,
            )
            counts = builder.build()
        finally:
            connection.close()
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    report = {
        "schema_version": IMPORTER_VERSION,
        "dataset_id": dataset_id,
        "game_version": game_version,
        "built_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database_path": str(output),
        "database_sha256": file_sha256(output),
        "source_payloads_included": include_source_payloads,
        "database_counts": counts,
        "foreign_key_violations": [],
    }
    (report_dir / "static_database_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (report_dir / "static_database_report.md").write_text(
        render_report(report), encoding="utf-8"
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--dataset-id", required=True)
    parser.add_argument("--game-version", default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument(
        "--omit-source-payloads",
        action="store_true",
        help="发行数据库不保存来源行原文，只保留行键和 SHA-256",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = build_database(
        args.source,
        args.output,
        args.report_dir,
        dataset_id=args.dataset_id,
        game_version=args.game_version,
        as_of=args.as_of,
        overrides_path=args.overrides,
        include_source_payloads=not args.omit_source_payloads,
    )
    print(f"SQLite: {Path(args.output).resolve()}")
    print(f"Report: {Path(args.report_dir).resolve()}")
    print(json.dumps(report["database_counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
