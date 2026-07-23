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
import math
import os
import re
import sqlite3
import sys
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
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.recommended_weights import DEFAULT_RECOMMENDED_WEIGHTS

SCHEMA_PATHS = (
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "002_game_static.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "003_game_static_remove_game_version.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "004_game_static_character_awaken.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "005_game_static_character_growth.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "006_game_static_character_skills.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "007_game_static_skill_damage.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "008_game_static_combat_context.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "009_game_static_monster_binding.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "010_game_static_abyss_binding.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "011_game_static_recommended_weights.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "012_game_static_graduation_template.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "013_game_static_setting_defaults.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "014_game_static_character_shape_bonus.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "015_game_static_logical_character_shape_bonus.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "016_game_static_fork_refinement_parameter.sql",
)
SCHEMA_VERSION = 16
IMPORTER_VERSION = 16

TABLE_PATHS = {
    "character": "DataTable/Character/DT_Character.json",
    "character_abilities": "DataTable/Character/DT_CharacterAbilityConfig.json",
    "character_ability_effects": "DataTable/Character/DT_CharacterAbilityEffectConfig.json",
    "skill_damage": "DataTable/skill/DT_SkillDamageData.json",
    "skill_damage_modifiers": "DataTable/skill/DT_SkillDamageGameplayModifyData.json",
    "combat_global_curves": "DataTable/skill/GlobalCharacterData/DT_GlobalCommonData.json",
    "reaction_damage": "DataTable/Reaction/DT_ReactionDamageData.json",
    "reaction_definitions": "DataTable/Reaction/DT_ReactionData.json",
    "reaction_constants": "DataTable/Reaction/DT_ReactionEffectFigure.json",
    "player_pack": "DataTable/PackData/DT_PlayerPackData.json",
    "player_modify": "DataTable/PackData/ModifyData/DT_PlayerModifyPackData.json",
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
    "fork_buff_curves": "DataTable/Fork/CT_ForkBuff.json",
    "fork_breakthroughs": "DataTable/Fork/DT_ForkBreakthroughData.json",
    "fork_modify": "DataTable/PackData/ModifyData/DT_ForkModifyData.json",
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

AWAKEN_DIRECTORY = Path("DataTable/Character/Awaken")
CHARACTER_PANEL_PROPERTIES = ("HPMaxBase", "AtkBase", "DefBase")
CHARACTER_BREAKTHROUGH_LEVELS = (20, 30, 40, 50, 60, 70)
CHARACTER_MAX_LEVEL = 80
ADDITIVE_MODIFIER_OPERATION = "EModifyModOp::MODIFY_MODOP_ADDITIVE"

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
        as_of: date,
        overrides_path: Path,
        include_source_payloads: bool = True,
    ) -> None:
        self.connection = connection
        self.content_root = content_root
        self.dataset_id = dataset_id
        self.as_of = as_of
        self.overrides_path = overrides_path
        self.include_source_payloads = include_source_payloads
        self.rows: dict[str, dict[str, Any]] = {}
        self.source_row_ids: dict[tuple[str, str], int] = {}
        self.awaken_rows: dict[int, tuple[dict[str, Any], int]] = {}

    def build(self) -> dict[str, Any]:
        for schema_path in SCHEMA_PATHS:
            self.connection.executescript(schema_path.read_text(encoding="utf-8"))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.connection.execute(
            "INSERT INTO schema_migration VALUES (2, ?)",
            (now,),
        )
        self.connection.execute("INSERT INTO schema_migration VALUES (3, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (4, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (5, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (6, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (7, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (8, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (9, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (10, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (11, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (12, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (13, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (14, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (15, ?)", (now,))
        self.connection.execute("INSERT INTO schema_migration VALUES (16, ?)", (now,))
        self.connection.execute(
            "INSERT INTO dataset VALUES (?, ?, ?)",
            (self.dataset_id, IMPORTER_VERSION, now),
        )
        self._mirror_sources()
        self._mirror_awaken_sources()
        self._import_characters()
        self._import_character_awakens()
        self._import_character_panel_growth()
        self._import_character_skills()
        self._import_skill_damage()
        self._import_combat_context()
        self._import_enemy_combat_profiles()
        self._import_monster_instance_profiles()
        self._import_abyss_bindings()
        self._import_equipment_attributes()
        self._import_equipment_shapes()
        self._import_equipment_suits()
        self._import_equipment_items()
        self._import_equipment_progression()
        self._import_equipment_plans()
        self._import_default_character_weights()
        self._import_forks()
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

    def _import_default_character_weights(self) -> None:
        """Seed every playable role; the developer API sync replaces available rows."""

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        character_ids = [
            int(row[0])
            for row in self.connection.execute(
                "SELECT character_id FROM equipment_plan ORDER BY character_id"
            )
        ]
        for character_id in character_ids:
            self.connection.execute(
                "INSERT INTO character_weight_recommendation VALUES (?, 'default', NULL, NULL, ?)",
                (character_id, now),
            )
            self.connection.executemany(
                """INSERT INTO character_weight_recommendation_property(
                       character_id, property_id, weight, main_weight, ordinal
                   ) VALUES (?, ?, ?, ?, ?)""",
                [
                    (character_id, property_id, weight, weight, ordinal)
                    for ordinal, (property_id, weight) in enumerate(DEFAULT_RECOMMENDED_WEIGHTS)
                ],
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
                    "INSERT INTO enemy_combat_profile VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        profile_set, pack_id, float_value(row, "DefBase"),
                        float_value(row, "DefUp"), float_value(row, "DefAdd"),
                        float_value(row, "DefIgnore"), float_value(row, "UnbalMax"),
                        float_value(row, "UnbalAccrueEfficiencyBase"),
                        float_value(row, "UnbalAntiAccrueEfficiencyBase"),
                        float_value(row, "UnbaleBonus"), float_value(row, "UnbalReduceNatur"),
                        float_value(row, "UnbalReduceReset"), self.source_row_id(table_name, pack_id),
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
                self.connection.execute(
                    "INSERT INTO abyss_monster_pool_entry VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        monster_pool_id, ordinal, asset_path(monster.get("MonsterClass")),
                        monster_count, monster_level, "standard", attribute_pack_id,
                        pool_source_row_id, self.source_row_id("monster_pack", attribute_pack_id),
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
    as_of: date,
    overrides_path: Path = DEFAULT_OVERRIDES,
    config_dir: Path = PROJECT_ROOT / "config",
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
                as_of=as_of,
                overrides_path=overrides_path,
                include_source_payloads=include_source_payloads,
            )
            counts = builder.build()
            try:
                from .build_graduation_templates import (
                    populate_logical_character_shape_bonuses,
                    populate_graduation_templates,
                )
            except ImportError:
                from build_graduation_templates import (
                    populate_logical_character_shape_bonuses,
                    populate_graduation_templates,
                )
            counts["logical_character_shape_bonus"] = populate_logical_character_shape_bonuses(
                connection,
                config_dir=config_dir.expanduser().resolve(),
            )
            counts["logical_character_shape_bonus_property"] = int(
                connection.execute(
                    "SELECT COUNT(*) FROM logical_character_shape_bonus_property"
                ).fetchone()[0]
            )
            counts["character_shape_bonus"] = 0
            counts["character_shape_bonus_property"] = 0
            connection.commit()
            counts["character_graduation_template"] = populate_graduation_templates(
                connection,
                database_path=temporary,
                config_dir=config_dir.expanduser().resolve(),
            )
        finally:
            connection.close()
        os.replace(temporary, output)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    report = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
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
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--config-dir", type=Path, default=PROJECT_ROOT / "config")
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
        as_of=args.as_of,
        overrides_path=args.overrides,
        config_dir=args.config_dir,
        include_source_payloads=not args.omit_source_payloads,
    )
    print(f"SQLite: {Path(args.output).resolve()}")
    print(f"Report: {Path(args.report_dir).resolve()}")
    print(json.dumps(report["database_counts"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
