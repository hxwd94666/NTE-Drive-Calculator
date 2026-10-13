# 在同一冻结方案内执行诊断装配或极速派发，保留步骤游标与副作用边界。
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack, nullcontext
from typing import Any

from src.services.equipment.apply import (
    EquipmentApplyError,
    EquipmentApplyResult,
    _item_uid,
    FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS,
)
from src.domain.equipment.apply_verification import plan_mismatch, module_plan_mismatch
from src.utils.logger import logger


def execute_plan(
    self,
    plan_id: int,
    *,
    character_uid: Mapping[str, Any] | None = None,
    target_character_id: int | None = None,
    timeout: float = 30.0,
    verify_after_dispatch: bool = True,
    exact_loadout: bool = False,
    force_dispatch: bool = False,
    reset_before_apply: bool = False,
    stable_snapshot_id: int | None = None,
) -> EquipmentApplyResult:
    """执行方案。

    ``verify_after_dispatch`` 适合诊断或登录页抓包可用的环境，会等待新
    稳定背包快照并逐项确认。游戏内极速装配则只依赖已有快照做前置校验；
    指令成功下发后立即返回，不能把登录时才会出现的背包快照当作成功条件。
    """

    if timeout <= 0:
        raise ValueError("timeout 必须大于 0")
    hello = self.sync_service.core_hello_result or {}
    capabilities = hello.get("capabilities", [])
    if not isinstance(capabilities, list) or "equipment" not in capabilities:
        raise EquipmentApplyError("当前 nte-core 不支持 equipment 能力")
    if stable_snapshot_id is None:
        before_snapshot_id = self.require_stable_snapshot()
    else:
        before_snapshot_id = int(stable_snapshot_id)
        if self.user_dao.inventory_snapshot_summary(before_snapshot_id) is None:
            raise EquipmentApplyError("指定的稳定背包快照不存在")

    plan = self.validate_plan_for_fast_apply(
        plan_id,
        stable_snapshot_id=before_snapshot_id,
    )
    effective_character_id = int(plan["character_id"] if target_character_id is None else target_character_id)
    assignments = plan["assignments"]
    modules = [item for item in assignments if item["kind"] == "module"]
    cores = [item for item in assignments if item["kind"] == "core"]

    current_items = self.user_dao.list_inventory_items(before_snapshot_id)
    by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in current_items}
    selected_uids: set[tuple[int, int]] = set()
    placements: list[dict[str, Any]] = []
    for index, assignment in enumerate(modules):
        uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
        if uid_pair in selected_uids:
            raise EquipmentApplyError("方案中存在重复装备 UID")
        selected_uids.add(uid_pair)
        item = by_uid.get(uid_pair)
        if item is None or item["kind"] != "module":
            raise EquipmentApplyError(f"方案驱动 UID {uid_pair} 不在当前稳定背包中")
        if assignment.get("rotation") not in (None, 0):
            raise EquipmentApplyError("nte-core 一键装配不接受旋转参数")
        row = assignment.get("target_row")
        column = assignment.get("target_column")
        if row not in range(1, 6) or column not in range(1, 6):
            raise EquipmentApplyError(f"第 {index + 1} 个驱动位置必须在 1..5")
        placements.append(
            {
                "equipment": _item_uid(item),
                "row": row,
                "column": column,
            }
        )
    core_assignment = cores[0] if cores else None
    core_item = None
    if core_assignment is not None:
        core_pair = (core_assignment["uid_serial"], core_assignment["uid_slot"])
        if core_pair in selected_uids:
            raise EquipmentApplyError("方案中存在重复装备 UID")
        core_item = by_uid.get(core_pair)
        if core_item is None or core_item["kind"] != "core":
            raise EquipmentApplyError(f"方案核心 UID {core_pair} 不在当前稳定背包中")
        if core_assignment.get("rotation") not in (None, 0):
            raise EquipmentApplyError("核心不能包含旋转参数")

    resolved_character_uid = self.resolve_character_uid(effective_character_id, before_snapshot_id, character_uid)
    current_mismatch = (
        plan_mismatch(
            items=current_items,
            modules=modules,
            core_assignment=core_assignment,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
        if core_assignment is not None or exact_loadout
        else module_plan_mismatch(
            items=current_items,
            modules=modules,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
    )
    # 普通装配可跳过冻结快照已匹配的方案；全卸空模式仍须先清空，不能共用此跳过条件。
    if current_mismatch is None and not force_dispatch and not reset_before_apply:
        return EquipmentApplyResult(
            plan_id=plan["plan_id"],
            before_snapshot_id=before_snapshot_id,
            after_snapshot_id=before_snapshot_id,
            character_uid=resolved_character_uid,
            rpc_result={"status": "already_applied"},
            already_applied=True,
        )

    if not verify_after_dispatch:
        return execute_fast_plan(
            self,
            plan=plan,
            snapshot_id=before_snapshot_id,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
            placements=placements,
            modules=modules,
            core_assignment=core_assignment,
            current_items=current_items,
            timeout=timeout,
            reset_before_apply=reset_before_apply,
        )

    with ExitStack() as dispatch_scope:
        self._dispatch_with_busy_retry(
            lambda: dispatch_scope.enter_context(self.sync_service.equipment_batch()),
            operation="等待装配批次就绪",
        )
        reset_target = reset_before_apply
        if reset_target:
            if current_mismatch is None:
                logger.info("角色 {} 按全卸空模式重装", effective_character_id)
            else:
                logger.info(
                    "角色 {} 当前配装不匹配（{}），先卸下全部装备后重装",
                    effective_character_id,
                    current_mismatch,
                )
            self._dispatch_with_busy_retry(
                lambda: self.sync_service.unequip_all(character=resolved_character_uid),
                operation="卸下角色现有装备",
                settle_seconds=0.7,
            )

        if core_item is not None:
            rpc_result = self._dispatch_with_busy_retry(
                lambda: self.sync_service.equip_one_key(
                    character=resolved_character_uid,
                    placements=placements,
                    core=_item_uid(core_item),
                    timeout=timeout,
                ),
                operation="一键装配",
            )
            dispatch_scope.close()
            after_state = self.sync_service.wait_for_snapshot(
                after_snapshot_id=before_snapshot_id,
                timeout=timeout,
            )
            after_snapshot_id = after_state.last_snapshot_id
        else:
            rpc_result = []
            after_snapshot_id = before_snapshot_id
            for placement, assignment in zip(placements, modules):
                source_item = by_uid[(assignment["uid_serial"], assignment["uid_slot"])]
                source_is_reset_target = (
                    source_item["equipped"] and source_item.get("equipped_character_uid") == resolved_character_uid
                )
                move_existing = bool(source_item["equipped"] and not (reset_target and source_is_reset_target))
                dispatcher = (
                    self.sync_service.move_module_to_character if move_existing else self.sync_service.equip_module
                )
                dispatch_name = "移动已装备驱动" if move_existing else "装备驱动"
                rpc_item_result = self._dispatch_with_busy_retry(
                    lambda: dispatcher(
                        character=resolved_character_uid,
                        equipment=placement["equipment"],
                        row=placement["row"],
                        column=placement["column"],
                    ),
                    operation=dispatch_name,
                )
                rpc_result.append(rpc_item_result)
                logger.info(
                    "角色 {} 驱动 {}/{} 已串行下发，方式={}",
                    effective_character_id,
                    len(rpc_result),
                    len(modules),
                    dispatch_name,
                )
                # 组件只容纳一条执行中和一条排队请求；每个驱动后等待观测，不塞满队列。
                dispatch_scope.close()
                after_state = self.sync_service.wait_for_snapshot(
                    after_snapshot_id=after_snapshot_id,
                    timeout=timeout,
                )
                after_snapshot_id = after_state.last_snapshot_id
    if after_snapshot_id is None or after_snapshot_id <= before_snapshot_id:
        raise EquipmentApplyError("核心组件没有返回装配后的新稳定快照")

    mismatch = (
        plan_mismatch(
            items=self.user_dao.list_inventory_items(after_snapshot_id),
            modules=modules,
            core_assignment=core_assignment,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
        if core_assignment is not None or exact_loadout
        else module_plan_mismatch(
            items=self.user_dao.list_inventory_items(after_snapshot_id),
            modules=modules,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
        )
    )
    if mismatch is not None:
        raise EquipmentApplyError(f"新快照未确认目标配装：{mismatch}")
    return EquipmentApplyResult(
        plan_id=plan["plan_id"],
        before_snapshot_id=before_snapshot_id,
        after_snapshot_id=after_snapshot_id,
        character_uid=resolved_character_uid,
        rpc_result=rpc_result,
    )


def execute_fast_plan(
    service,
    *,
    plan,
    snapshot_id,
    character_id,
    character_uid,
    placements,
    modules,
    core_assignment,
    current_items,
    timeout,
    reset_before_apply,
):
    runtime = service.recovery
    if runtime is not None:
        runtime.begin_role(int(plan["plan_id"]), character_id, character_uid)

    def current_rows():
        return runtime.observed_items if runtime is not None and runtime.observed_items is not None else current_items

    def one_key_confirmed(rows):
        return (
            plan_mismatch(
                items=rows,
                modules=modules,
                core_assignment=core_assignment,
                character_id=character_id,
                character_uid=character_uid,
            )
            is None
        )

    def reset_confirmed(rows):
        return all(
            not row.get("equipped")
            or (isinstance(row.get("equipped_character_uid"), dict) and row["equipped_character_uid"] != character_uid)
            for row in rows
        )

    # 恢复owner持有外层批次并能释放；独立装配才自行打开批次。
    scope = (
        (nullcontext() if runtime.batch_active else runtime.batch())
        if runtime is not None
        else service.sync_service.equipment_batch()
    )
    reset_rows = None
    with scope:
        if reset_before_apply:
            logger.info("角色 {} 按全卸空模式重装", character_id)
            service._dispatch_with_busy_retry(
                lambda: service.sync_service.unequip_all(character=character_uid),
                operation="卸下角色现有装备",
                step="reset",
                settle_seconds=0.7,
                confirmed=reset_confirmed,
            )
            reset_rows = current_rows()
        if core_assignment is not None:
            result = service._dispatch_with_busy_retry(
                lambda: service.sync_service.equip_one_key(
                    character=character_uid,
                    placements=placements,
                    core={"slot": core_assignment["uid_slot"], "serial": core_assignment["uid_serial"]},
                    timeout=timeout,
                ),
                operation="一键装配",
                step="one_key",
                settle_seconds=FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS,
                confirmed=one_key_confirmed,
            )
        else:
            result = []
            for index, (placement, assignment) in enumerate(zip(placements, modules), 1):

                def dispatch_module(placement=placement, assignment=assignment):
                    pair = (assignment["uid_slot"], assignment["uid_serial"])
                    source = next(row for row in current_rows() if (row["uid_slot"], row["uid_serial"]) == pair)
                    was_reset_target = (
                        reset_before_apply
                        and source.get("equipped_character_uid") == character_uid
                        and current_rows() is reset_rows
                    )
                    move = bool(source.get("equipped") and not was_reset_target)
                    dispatcher = (
                        service.sync_service.move_module_to_character if move else service.sync_service.equip_module
                    )
                    return dispatcher(
                        character=character_uid,
                        equipment=placement["equipment"],
                        row=placement["row"],
                        column=placement["column"],
                    )

                response = service._dispatch_with_busy_retry(
                    dispatch_module,
                    operation="装配驱动",
                    step="module",
                    module_index=index,
                    settle_seconds=FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS,
                    confirmed=lambda rows, assignment=assignment: (
                        module_plan_mismatch(
                            items=rows, modules=[assignment], character_id=character_id, character_uid=character_uid
                        )
                        is None
                    ),
                )
                result.append(response)
                logger.info("角色 {} 驱动 {}/{} 已串行下发", character_id, index, len(modules))
    return EquipmentApplyResult(
        plan_id=plan["plan_id"],
        before_snapshot_id=snapshot_id,
        after_snapshot_id=snapshot_id,
        character_uid=character_uid,
        rpc_result=result,
        verified=False,
    )
