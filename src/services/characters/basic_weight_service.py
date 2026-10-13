# 编排基础权重目录、完整校验与账号事务；不读取 Qt 控件或 MainWindow 状态。
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from src.domain.basic_weight import BasicWeightDependencies
from src.domain.recommended_weights import base_weight_character_id
from src.domain.stat_catalog import StatCatalog
from src.integrations.basic_weight_inputs import basic_weight_source_key
from src.observability import OperationContext
from src.observability.operation import log_event, operation_scope
from src.services.characters.character_shape_bonus_service import DEFAULT_EXTRA_SHAPE_LABEL
from src.services.characters.character_weight_service import (
    apply_account_weight_change, ensure_account_character_weights,
    prepare_account_weight_change, reset_account_character_weights,
)
from src.services.characters.custom_character_service import create_custom_character, delete_custom_character
from src.services.official_role.page import load_official_role_index
from src.services.characters.workshop_weight_template_service import (
    configured_workshop_weight_template_file, load_workshop_weight_snapshot,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError


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


class BasicWeightService:
    """单一业务入口；读取不种默认值，提交不污染静态库或原方案评分。"""

    def __init__(self, dependencies: BasicWeightDependencies) -> None:
        self.dependencies = dependencies

    def source_key(self) -> tuple[tuple, ...]:
        return basic_weight_source_key(self.dependencies, configured_workshop_weight_template_file())

    def operation(self) -> OperationContext:
        return OperationContext.create(
            "basic_weight",
            account_id=self.dependencies.account_id,
            context_generation=self.dependencies.generation,
        )

    def load_form_data(self) -> dict[str, Any]:
        """从冻结路径解析目录与账号显式权重；开页不新增或刷新账号默认记录。"""
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

    def _load_form_data(self) -> dict[str, Any]:
        characters = load_official_role_index(
            self.dependencies.user_database_path,
            static_database_path=self.dependencies.static_database_path,
        )
        character_ids = [int(row["character_id"]) for row in characters]
        account_weights = ensure_account_character_weights(
            self.dependencies.user_database_path,
            character_ids,
            static_database_path=self.dependencies.static_database_path,
            persist_defaults=False,
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
            with UserDataDao(self.dependencies.user_database_path) as user_dao:
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

    def save_changes(
        self,
        data: dict,
        dirty_character_ids: set[int],
        dirty_shape_bonus_ids: set[int],
        dirty_board_ids: set[int] | None = None,
        dirty_target_suit_ids: set[int] | None = None,
    ) -> None:
        """先校验整批，再由唯一 DAO 连接提交；SQL 失败不会留下部分角色或字段。"""
        data = deepcopy(data)
        weights, shapes, boards, targets = (
            {int(value) for value in field}
            for field in (dirty_character_ids, dirty_shape_bonus_ids, dirty_board_ids or (), dirty_target_suit_ids or ())
        )
        requested = weights | shapes | boards | targets
        if not requested:
            return
        by_id = {}
        for role in data.values():
            cid = int(role["character_id"])
            if cid in by_id:
                raise UserDataValidationError("同一角色出现在多个编辑记录中")
            by_id[cid] = role
        if not requested.issubset(by_id):
            raise UserDataValidationError("提交缺少对应角色的冻结编辑记录")
        with operation_scope(
            self.operation(), started_event="basic_weight.save_started",
            succeeded_event="basic_weight.save_succeeded", failed_event="basic_weight.save_failed",
            message="保存角色基础权重设置", requested_character_count=len(requested),
        ):
            with (StaticGameDataDao(self.dependencies.static_database_path) as static,
                  UserDataDao(self.dependencies.user_database_path) as user, user.write_transaction()):
                custom_ids = {int(role["character_id"]) for role in user.list_custom_characters()}
                for cid in requested:
                    if cid not in custom_ids and static.get_character(cid) is None:
                        raise UserDataValidationError("编辑角色已不存在；请重新载入")
                if not (shapes | boards | targets).issubset(custom_ids):
                    raise UserDataValidationError("只有自建角色可编辑底盘、默认套装和额外形状")
                # 计划只含实际脏字段；按正式 ID 去重主角共享权重，不接受相互矛盾的覆盖顺序。
                plans, shape_plans, board_plans, target_plans = {}, {}, {}, {}
                # 全批共用一次公开默认值解析，更新线程原子替换模板也不会混入不同版本。
                workshop = load_workshop_weight_snapshot() if weights else None
                for cid in sorted(weights):
                    role = by_id[cid]
                    change = prepare_account_weight_change(
                        user, static, cid, role.get("weights") or {}, role.get("main_weights") or {},
                        workshop_snapshot=workshop,
                    )
                    source_id = base_weight_character_id(cid)
                    previous = plans.get(source_id)
                    if previous is not None and previous.properties != change.properties:
                        raise UserDataValidationError("共享基础权重的角色编辑冲突；请统一数值后保存")
                    plans[source_id] = change
                known_properties = {str(row["attribute_id"]) for row in static.list_equipment_attributes()}
                for cid in sorted(shapes):
                    role = by_id[cid]
                    values = role.get("extra_shape_buffs") or {}
                    if not set(values).issubset(known_properties):
                        raise UserDataValidationError("额外形状包含静态资料未确认的属性")
                    shape_plans[cid] = user.validate_custom_character_shape_bonus(
                        str(role.get("extra_shape_label") or ""), values,
                    )
                for cid in sorted(boards):
                    board_plans[cid] = user.validate_custom_character_board_cells(
                        list(by_id[cid].get("board_cells") or ()),
                    )
                known_suits = {str(row["suit_id"]) for row in static.list_suits()}
                for cid in sorted(targets):
                    suit_id = str(by_id[cid].get("target_suit_id") or "").strip() or None
                    if suit_id is not None and suit_id not in known_suits:
                        raise UserDataValidationError("默认套装不在本次静态资料中")
                    target_plans[cid] = suit_id
                # 这里才开始写入；内层 DAO 保存点均不提前提交本次完整事务。
                for change in plans.values():
                    apply_account_weight_change(user, change)
                for cid, cells in board_plans.items():
                    user.save_custom_character_board_cells(cid, cells)
                for cid, suit_id in target_plans.items():
                    user.save_custom_character_target_suit_id(cid, suit_id)
                for cid, (label, property_id, value) in shape_plans.items():
                    user.save_custom_character_shape_bonus(
                        cid, shape_label=label, property_values={property_id: value} if property_id else {},
                    )


    def create_custom_role(self, name_zh: str) -> dict:
        """复用角色与初始权重的完整事务，不在 Controller 拼接多个独立提交。"""
        return create_custom_character(self.dependencies.user_database_path, name_zh)

    def delete_custom_role(self, character_id: int) -> None:
        delete_custom_character(self.dependencies.user_database_path, character_id)

    def reset_weights(self, character_ids: Iterable[int]) -> dict[int, dict]:
        """冻结角色集合后整批恢复默认值，不影响自建角色或原方案评分。"""
        return reset_account_character_weights(
            self.dependencies.user_database_path,
            tuple(int(character_id) for character_id in character_ids),
            operation_context=self.operation(),
            static_database_path=self.dependencies.static_database_path,
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
