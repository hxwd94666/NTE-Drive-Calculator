# 编排基础权重与公共额外形状的加载、保存、重置及关联日志。
"""Controller boundary for account weights and shared shape overrides."""

from __future__ import annotations

from collections.abc import Iterable

from src.domain.stat_catalog import StatCatalog
from src.features.configuration.dependencies import BasicWeightDependencies
from src.observability import OperationContext
from src.observability.operation import log_event, operation_scope
from src.services.character_shape_bonus_service import (
    DEFAULT_EXTRA_SHAPE_LABEL,
    get_effective_character_shape_bonus,
    save_public_character_shape_bonus,
)
from src.services.character_weight_service import (
    ensure_account_character_weights,
    reset_account_character_weights,
    save_account_character_weights,
)
from src.services.custom_character_service import (
    create_custom_character,
    delete_custom_character,
    save_custom_character_board,
    save_custom_character_shape_bonus,
    save_custom_character_target_suit,
)
from src.services.official_role_page_service import load_official_role_index
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


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


class BasicWeightController:
    """Own the account-pinned boundary consumed by the Qt form."""

    def __init__(self, dependencies: BasicWeightDependencies) -> None:
        self.dependencies = dependencies

    def operation(self) -> OperationContext:
        return OperationContext.create(
            "basic_weight",
            account_id=self.dependencies.account_id,
            context_generation=self.dependencies.generation,
        )

    def load_form_data(self) -> dict[str, object]:
        operation = self.operation()
        with operation_scope(
            operation,
            started_event="basic_weight.load_started",
            succeeded_event="basic_weight.load_succeeded",
            failed_event="basic_weight.load_failed",
            message="加载角色基础权重",
        ) as span:
            result = self._load_form_data()
            roles = result["roles"]
            span.annotate(
                role_count=len(roles) if isinstance(roles, dict) else 0
            )
            return result

    def _load_form_data(self) -> dict[str, object]:
        characters = load_official_role_index(
            self.dependencies.user_database_path
        )
        character_ids = [int(row["character_id"]) for row in characters]
        account_weights = ensure_account_character_weights(
            self.dependencies.user_database_path,
            character_ids,
        )
        with StaticGameDataDao(self.dependencies.static_database_path) as static_dao:
            attributes = {
                str(row["attribute_id"]): row
                for row in static_dao.list_equipment_attributes()
            }
            known_property_ids = set(attributes)
            stats_catalog = StatCatalog.from_config_dir(
                self.dependencies.config_dir
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
            roles = {}
            for character in characters:
                character_id = int(character["character_id"])
                record = account_weights.get(character_id) or {}
                shape_bonus = (
                    get_effective_character_shape_bonus(
                        static_dao,
                        character_id,
                        shared_database_path=self.dependencies.shared_database_path,
                    )
                    or {}
                )
                roles[str(character.get("name_zh") or character_id)] = {
                    "character_id": character_id,
                    "source_kind": str(record.get("source_kind") or "default"),
                    "shape_bonus_source": str(
                        shape_bonus.get("effective_source") or "static_default"
                    ),
                    "extra_shape_label": str(
                        shape_bonus.get("shape_label") or DEFAULT_EXTRA_SHAPE_LABEL
                    ),
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
            with UserDataDao(self.dependencies.user_database_path) as user_dao:
                for custom in user_dao.list_custom_characters():
                    character_id = int(custom["character_id"])
                    record = user_dao.get_character_weight_preferences(character_id) or {}
                    shape_bonus = custom.get("shape_bonus") or {}
                    roles[str(custom["name_zh"])] = {
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

    def save_changes(
        self,
        data: dict,
        dirty_character_ids: set[int],
        dirty_shape_bonus_ids: set[int],
        dirty_board_ids: set[int] | None = None,
        dirty_target_suit_ids: set[int] | None = None,
    ) -> None:
        operation = self.operation()
        for role_data in data.values():
            character_id = int(role_data["character_id"])
            if character_id in dirty_character_ids:
                save_account_character_weights(
                    self.dependencies.user_database_path,
                    character_id,
                    role_data.get("weights") or {},
                    main_property_weights=role_data.get("main_weights") or {},
                    operation_context=operation,
                )
            if character_id in (dirty_board_ids or set()):
                save_custom_character_board(
                    self.dependencies.user_database_path,
                    character_id,
                    list(role_data.get("board_cells") or ()),
                )
            if character_id in (dirty_target_suit_ids or set()):
                save_custom_character_target_suit(
                    self.dependencies.user_database_path,
                    character_id,
                    str(role_data.get("target_suit_id") or "") or None,
                )
            if character_id in dirty_shape_bonus_ids:
                if role_data.get("is_custom"):
                    save_custom_character_shape_bonus(
                        self.dependencies.user_database_path,
                        character_id,
                        shape_label=str(role_data.get("extra_shape_label") or ""),
                        property_values=role_data.get("extra_shape_buffs") or {},
                    )
                else:
                    save_public_character_shape_bonus(
                        character_id,
                        shape_label=str(role_data.get("extra_shape_label") or ""),
                        property_values=role_data.get("extra_shape_buffs") or {},
                        database_path=self.dependencies.static_database_path,
                        shared_database_path=self.dependencies.shared_database_path,
                        operation_context=operation,
                    )

    def create_custom_role(self, name_zh: str) -> dict:
        return create_custom_character(self.dependencies.user_database_path, name_zh)

    def delete_custom_role(self, character_id: int) -> None:
        delete_custom_character(self.dependencies.user_database_path, character_id)

    def reset_weights(self, character_ids: Iterable[int]) -> list[int]:
        return reset_account_character_weights(
            self.dependencies.user_database_path,
            tuple(int(character_id) for character_id in character_ids),
            operation_context=self.operation(),
        )

    def log_dirty_exit(self, action: str, dirty_count: int) -> None:
        log_event(
            "INFO",
            "basic_weight.dirty_exit_decided",
            "处理基础权重页面未保存修改",
            self.operation(),
            action=action,
            dirty_character_count=int(dirty_count),
        )
