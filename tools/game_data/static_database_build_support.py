# 从准备好的游戏官方文件目录构建版本化静态 SQLite 数据库。
# ruff: noqa: F401
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
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "017_game_static_combat_catalog.sql",
    PROJECT_ROOT / "src" / "storage" / "sqlite" / "schema" / "018_game_static_character_likeability.sql",
)
SCHEMA_VERSION = 18
IMPORTER_VERSION = 18

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
    "cultivation_guides": "DataTable/Character/CultivationGuide/DT_CultivationGuideData.json",
    "gameplay_ability_tips": "DataTable/Skill/DT_GameplayAbilityTipsData.json",
    "gameplay_effect_mapping": "DataTable/Skill/DT_GameplayEffectMappingData.json",
    "monster_manual": "DataTable/DT_MonsterManualConfig.json",
    "equipment_modify": "DataTable/PackData/ModifyData/DT_EquipmentModifyData.json",
    "equipment_buff_curves": "DataTable/Equipment/CT_Equipmentbuff.json",
    "likeability_roles": "DataTable/LikeabilitySystem/DT_LikeabilityRoleData.json",
    "likeability_modify": "DataTable/PackData/ModifyData/DT_LikeabilityModifyData.json",
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


def write_static_manifest(
    database_path: str | Path,
    manifest_path: str | Path,
) -> dict[str, Any]:
    """从实际发行库生成机器可读清单。"""

    database = Path(database_path).expanduser().resolve()
    uri = f"{database.as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        dataset = connection.execute(
            """SELECT dataset_id, importer_version, built_at_utc
               FROM dataset"""
        ).fetchone()
        schema = connection.execute(
            "SELECT MAX(version) FROM schema_migration"
        ).fetchone()
        payload_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM source_row WHERE payload_json IS NOT NULL"
            ).fetchone()[0]
        )
    if dataset is None or schema is None or schema[0] is None:
        raise RuntimeError(f"静态数据库缺少发行清单所需元数据：{database}")
    manifest = {
        "format_version": 1,
        "database": {
            "filename": database.name,
            "dataset_id": str(dataset[0]),
            "schema_version": int(schema[0]),
            "sha256": file_sha256(database).upper(),
            "generated_at_utc": str(dataset[2]),
            "source_payloads_omitted": payload_count == 0,
        },
        "build_tool": {
            "path": "tools/game_data/build_static_database.py",
            "importer_version": int(dataset[1]),
        },
    }
    output = Path(manifest_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


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


def show_time(row: dict[str, Any]) -> str | None:
    """Return the official mainland availability time for one character row."""

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
