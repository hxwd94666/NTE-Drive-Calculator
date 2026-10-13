# 编排配装管理的冻结读取与单次提交，数据库事务和SQL留在DAO。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from types import MappingProxyType

from src.services.equipment.game_loadout_projection_service import (
    GameLoadoutImportRequest, GameLoadoutProjectionService,
)
from src.domain.equipment.loadout_management import (
    LoadoutManagementSnapshot, LoadoutMutation, ManagedSlot,
)
from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True)
class GameLoadoutMutation:
    snapshot: LoadoutManagementSnapshot
    requests: tuple[GameLoadoutImportRequest, ...]


def freeze_game_loadout_request(request):
    """切断展示层可变字典引用，冻结来源、摆放和本次逐件分，不再重评分。"""
    projection = replace(request.projection,
        items=tuple(MappingProxyType(deepcopy(row)) for row in request.projection.items),
        assignments=tuple(MappingProxyType(deepcopy(row)) for row in request.projection.assignments))
    return replace(request, projection=projection,
                   assignment_scores=MappingProxyType(dict(request.assignment_scores)))


class LoadoutManagementService:
    def __init__(self, dao_factory=UserDataDao):
        self._dao_factory = dao_factory

    def prepare(self, operation, *, slot_id=None, plan_id=None):
        operation.checkpoint()
        account = operation.account
        with self._dao_factory(account.database_path) as dao:
            with dao.read_consistent_state():
                rows = dao.list_visible_loadout_slots_with_plans()
        slots = tuple(ManagedSlot.from_row(row) for row in rows)
        character_id = None
        if slot_id is not None or plan_id is not None:
            target = next((slot for slot in slots if (
                slot.slot_id == slot_id if slot_id is not None else slot.plan_id == plan_id
            )), None)
            if target is None:
                raise RuntimeError("当前配装槽位或方案已变化，请刷新页面。")
            character_id = target.character_id
            slots = tuple(slot for slot in slots if slot.character_id == character_id)
        operation.checkpoint()
        return LoadoutManagementSnapshot(account, slots, character_id)

    def mutate(self, operation, mutation: LoadoutMutation):
        operation.checkpoint()
        if mutation.snapshot.account != operation.account:
            raise RuntimeError("配装请求的账号身份已失效，请刷新页面。")
        with self._dao_factory(operation.account.database_path) as dao:
            if isinstance(mutation, GameLoadoutMutation):
                return self._import_game(dao, operation, mutation)
            # 提交后不再取消或重读：物理成功由返回值交付，页面恢复失败单独处理。
            return dao.mutate_loadout_management(
                mutation, checkpoint=operation.checkpoint,
                commit_context=operation.commit_context,
            )

    def _import_game(self, dao, operation, mutation):
        if mutation.snapshot.character_id is not None:
            raise RuntimeError("导入缺少全部槽位与锁的冻结范围")
        for request in mutation.requests:
            slots = tuple(slot for slot in mutation.snapshot.slots
                          if slot.character_id == request.projection.character_id)
            if request.slot_id is None:
                if slots or not request.create_slot_name:
                    raise RuntimeError("导入目标槽位已失效，请重新选择")
            elif not any(slot.slot_id == request.slot_id for slot in slots):
                raise RuntimeError("导入目标槽位不属于冻结角色")
        checked = False

        def validate():
            nonlocal checked
            operation.checkpoint()
            # 写事务内首次核对完整旧状态；末端不可拿已被本事务改变的槽位对比旧值。
            if not checked:
                dao.assert_loadout_management_snapshot(mutation.snapshot)
                checked = True

        # 保存只消费冻结投影，不读取静态库或重复求解；原有UID/借出/锁事务原样复用。
        return GameLoadoutProjectionService(dao).import_roles(mutation.requests,
            checkpoint=operation.checkpoint, validate=validate,
            commit_context=operation.commit_context, frozen_slots=True)
