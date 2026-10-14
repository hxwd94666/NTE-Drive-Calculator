# 编排基础权重读取、整批校验与唯一账号事务；目录物化由同包 catalog 完成。
from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from src.domain.basic_weight import BasicWeightDependencies
from src.domain.recommended_weights import base_weight_character_id
from src.integrations.basic_weight_inputs import basic_weight_source_key
from src.observability import OperationContext
from src.observability.operation import log_event, operation_scope
from src.services.characters.character_weight_service import (
    apply_account_weight_change, prepare_account_weight_change, reset_account_character_weights,
)
from src.services.characters.custom_character_service import create_custom_character, delete_custom_character
from src.services.characters.workshop_weight_template_service import (
    configured_workshop_weight_template_file, load_workshop_weight_snapshot,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from .catalog import load_basic_weight_catalog


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
            result = load_basic_weight_catalog(self.dependencies)
            roles = result["roles"]
            span.annotate(
                role_count=len(roles) if isinstance(roles, dict) else 0
            )
            return result


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
