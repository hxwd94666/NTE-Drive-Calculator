# 组装基础权重编辑目录与正式属性选项，只读解析默认值与账号记录。
from __future__ import annotations

from typing import Any

from src.domain.basic_weight import BasicWeightDependencies
from src.domain.stat_catalog import StatCatalog
from src.services.characters.character_shape_bonus_service import DEFAULT_EXTRA_SHAPE_LABEL
from src.services.characters.character_weight_service import ensure_account_character_weights
from src.services.official_role.page import load_official_role_index
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


# 标签只是中文展示；真正的选项和草稿关联始终使用发行属性 ID。
_EXTRA_SHAPE_LABEL_CHOICES = ("Type-3", "Type-2", "Type-4")
_ACCOUNT_MAIN_PROPERTY_CHOICES = (
    ("生命值百分比", "HPMaxUp"),
    ("攻击力百分比", "AtkUp"),
    ("防御力百分比", "DefUp"),
    ("暴击率", "CritBase"),
    ("暴击伤害", "CritDamageBase"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
    ("治疗加成", "HealUp"),
    ("光属性异能伤害增强", "DamageUpCosmosBase"),
    ("灵属性异能伤害增强", "DamageUpNatureBase"),
    ("咒属性异能伤害增强", "DamageUpIncantationBase"),
    ("暗属性异能伤害增强", "DamageUpChaosBase"),
    ("魂属性异能伤害增强", "DamageUpPsycheBase"),
    ("相属性异能伤害增强", "DamageUpLakshanaBase"),
    ("心灵伤害增强", "DamageUpPsychicallyBase"),
)
_WEIGHT_POOL_PROPERTY_IDS = {
    "生命值%": "HPMaxUp",
    "生命值": "HPMaxAdd",
    "攻击力%": "AtkUp",
    "攻击力": "AtkAdd",
    "防御力%": "DefUp",
    "防御力": "DefAdd",
    "伤害增加%": "DamageUpGeneralBase",
    "暴击率%": "CritBase",
    "暴击伤害%": "CritDamageBase",
    "环合强度": "MagBase",
    "倾陷强度": "UnbalIntensityBase",
    "治疗加成%": "HealUp",
    "光属性异能伤害增强%": "DamageUpCosmosBase",
    "灵属性异能伤害增强%": "DamageUpNatureBase",
    "咒属性异能伤害增强%": "DamageUpIncantationBase",
    "暗属性异能伤害增强%": "DamageUpChaosBase",
    "魂属性异能伤害增强%": "DamageUpPsycheBase",
    "相属性异能伤害增强%": "DamageUpLakshanaBase",
    "心灵伤害增强%": "DamageUpPsychicallyBase",
}


def load_basic_weight_catalog(dependencies: BasicWeightDependencies) -> dict[str, Any]:
    # 同一冻结路径贯穿官方目录、账号只读权重和自建角色；不在开页时种默认记录。
    characters = load_official_role_index(
        dependencies.user_database_path,
        static_database_path=dependencies.static_database_path,
    )
    character_ids = [int(row["character_id"]) for row in characters]
    account_weights = ensure_account_character_weights(
        dependencies.user_database_path,
        character_ids,
        static_database_path=dependencies.static_database_path,
        persist_defaults=False,
    )
    with StaticGameDataDao(dependencies.static_database_path) as static_dao:
        attributes = {
            str(row["attribute_id"]): row
            for row in static_dao.list_equipment_attributes()
        }
        known_property_ids = set(attributes)
        stats_catalog = StatCatalog.from_config_dir(
            dependencies.config_dir
        )
        sub_choices = [
            (label, _WEIGHT_POOL_PROPERTY_IDS[label])
            for label in stats_catalog.tape_sub_stat_pool()
            if label in _WEIGHT_POOL_PROPERTY_IDS
            and _WEIGHT_POOL_PROPERTY_IDS[label] in known_property_ids
        ]
        main_choices = [
            (label, property_id)
            for label, property_id in _ACCOUNT_MAIN_PROPERTY_CHOICES
            if property_id in known_property_ids
        ]
        stats_weight_pool = stats_catalog.weight_choice_pool()
        weight_property_by_label: dict[str, str] = {}
        for attribute in attributes.values():
            property_id = str(attribute["attribute_id"])
            label = str(
                attribute.get("filter_name_zh")
                or attribute.get("display_name_zh")
                or property_id
            ).replace("百分比", "%")
            if bool(attribute.get("show_percent")) and not label.endswith("%"):
                label = f"{label}%"
            canonical_label = stats_catalog.normalize_stat_name(label) or label
            if canonical_label not in stats_weight_pool:
                continue
            existing = weight_property_by_label.get(canonical_label)
            preferred_property_id = _WEIGHT_POOL_PROPERTY_IDS.get(
                canonical_label
            )
            if existing is None or property_id == preferred_property_id:
                weight_property_by_label[canonical_label] = property_id
        shape_bonus_choices = [
            (stat_name, weight_property_by_label[stat_name])
            for stat_name in stats_weight_pool
            if stat_name in weight_property_by_label
        ]
        suit_choices = [
            (str(row.get("name_zh") or row["suit_id"]), str(row["suit_id"]))
            for row in static_dao.list_suits()
        ]
        # 显示名允许重复；正式 ID 才是页面草稿、删除和选中的身份。
        roles = {}
        for character in characters:
            character_id = int(character["character_id"])
            record = account_weights.get(character_id) or {}
            shape_bonus = static_dao.get_character_shape_bonus(character_id) or {}
            roles[str(character_id)] = {
                "name_zh": str(character.get("name_zh") or character_id),
                "character_id": character_id,
                "source_kind": str(record.get("source_kind") or "default"),
                "shape_bonus_source": "static_database",
                "extra_shape_label": str(shape_bonus.get("shape_label") or ""),
                "extra_shape_buffs": {
                    str(row["property_id"]): float(row["display_value"])
                    for row in shape_bonus.get("properties") or ()
                },
                "weights": {
                    str(property_id): float(weight)
                    for property_id, weight in (
                        record.get("property_weights") or {}
                    ).items()
                },
                "main_weights": {
                    str(property_id): float(weight)
                    for property_id, weight in (
                        record.get("main_property_weights") or {}
                    ).items()
                },
                "is_custom": False,
            }
        with UserDataDao(dependencies.user_database_path) as user_dao:
            for custom in user_dao.list_custom_characters():
                character_id = int(custom["character_id"])
                record = user_dao.get_character_weight_preferences(character_id) or {}
                shape_bonus = custom.get("shape_bonus") or {}
                roles[str(character_id)] = {
                    "name_zh": str(custom["name_zh"]),
                    "character_id": character_id,
                    "source_kind": "custom",
                    "shape_bonus_source": "account_custom",
                    "extra_shape_label": str(shape_bonus.get("shape_label") or DEFAULT_EXTRA_SHAPE_LABEL),
                    "extra_shape_buffs": {
                        str(row["property_id"]): float(row["display_value"])
                        for row in shape_bonus.get("properties") or ()
                    },
                    "weights": dict(record.get("property_weights") or {}),
                    "main_weights": dict(record.get("main_property_weights") or {}),
                    "is_custom": True,
                    "game_name": str(custom["game_name"]),
                    "target_suit_id": str(custom.get("target_suit_id") or ""),
                    "board_cells": [
                        {"row": int(cell["row_number"]), "column": int(cell["column_number"]), "is_enabled": bool(cell["is_enabled"]), "is_locked": bool(cell["is_locked"])}
                        for cell in custom["board_cells"]
                    ],
                }
    labels = {
        property_id: label
        for label, property_id in (*sub_choices, *main_choices)
    }
    labels.update(
        {
            property_id: label
            for label, property_id in shape_bonus_choices
        }
    )
    return {
        "roles": roles,
        "property_labels": labels,
        "sub_choices": sub_choices,
        "main_choices": main_choices,
        "shape_bonus_choices": shape_bonus_choices,
        "shape_label_choices": _EXTRA_SHAPE_LABEL_CHOICES,
        "suit_choices": suit_choices,
    }
