# 对 SQLite 装配方案执行一键装配，并可选择等待后续稳定快照验证结果。
"""本地核心组件一键装配的前置检查、调用和结果确认。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from copy import deepcopy
import time
from typing import Any, Protocol

from src.integrations.operation_guard import OperationGuard, require_operation
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

from src.domain.equipment.apply_verification import (
    module_plan_mismatch,
    plan_mismatch,
    scoped_plan_mismatch,
)
from src.services.inventory.sync.state import InventorySyncState
from .apply_recovery import retry_command


MAX_UID_COMPONENT = 4_294_967_295
_PROTAGONIST_CHARACTER_IDS = {1046: "male", 1051: "female"}
# 原生装备接口的成功响应表示请求已被接收；游戏线程完成装备变更
# 可能稍晚。极速模式没有可靠的逐条完成事件，因此在相邻装备命令之间保留
# 一个短执行窗口，避免驱动-only 方案连续塞入 8 条请求时中间指令被吞。
FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS = 0.5


class EquipmentApplyError(RuntimeError):
    """装配未满足安全前提，或新快照未能确认装配结果。"""


class _LiveInventorySync(Protocol):
    @property
    def state(self) -> InventorySyncState: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def core_hello_result(self) -> dict[str, Any] | None: ...

    def equipment_batch(self) -> AbstractContextManager: ...

    def equip_one_key(self, **kwargs: Any) -> Any: ...

    def equip_module(self, **kwargs: Any) -> Any: ...

    def unequip_module(self, **kwargs: Any) -> Any: ...

    def unequip_core(self, **kwargs: Any) -> Any: ...

    def unequip_all(self, **kwargs: Any) -> Any: ...

    def move_module_to_character(self, **kwargs: Any) -> Any: ...

    def wait_for_snapshot(
        self, *, after_snapshot_id: int | None = None, timeout: float = 30.0
    ) -> InventorySyncState: ...


@dataclass(frozen=True)
class EquipmentApplyResult:
    """由当前或后续稳定背包快照确认，或已下发的一键装配结果。"""

    plan_id: int
    before_snapshot_id: int
    after_snapshot_id: int
    character_uid: dict[str, int]
    rpc_result: Any
    verified: bool = True
    already_applied: bool = False


def _uid(value: Mapping[str, Any], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for component in ("slot", "serial"):
        raw = value.get(component)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise EquipmentApplyError(f"{field}.{component} 必须是整数")
        if raw <= 0 or raw >= MAX_UID_COMPONENT:
            raise EquipmentApplyError(
                f"{field}.{component} 必须在 1 到 {MAX_UID_COMPONENT - 1} 之间"
            )
        result[component] = raw
    return result


def _item_uid(item: Mapping[str, Any]) -> dict[str, int]:
    return _uid(
        {"slot": item["uid_slot"], "serial": item["uid_serial"]},
        "equipment",
    )


class EquipmentApplyService:
    """把已保存方案交给持续运行的核心组件，并确认最终背包状态。"""

    def __init__(
        self, user_dao: UserDataDao, sync_service: _LiveInventorySync,
        *, operation_guard: OperationGuard | None = None,
    ) -> None:
        self.user_dao = user_dao
        self.sync_service = sync_service
        self.operation_guard = operation_guard
        self.recovery = None
        self.dispatch_started = False
        self.execution_check = lambda: require_operation(self.operation_guard, "native_equipment")
        self._frozen_plans: dict[int, dict[str, Any]] = {}

    def freeze_plans(self, roles: list[Mapping[str, Any]], snapshot_id: int):
        """一次深拷贝冻结目标方案；后续快照只用于确认，不补入新目标。"""
        plans = {int(role["plan_id"]): deepcopy(self.validate_plan_for_fast_apply(
            int(role["plan_id"]), stable_snapshot_id=snapshot_id)) for role in roles}
        self._frozen_plans = plans
        return deepcopy(plans)

    def read_plan(self, plan_id: int):
        return deepcopy(self._frozen_plans.get(int(plan_id))) if int(plan_id) in self._frozen_plans else self.user_dao.get_loadout_plan(plan_id)

    @staticmethod
    def _uid_pair(value: Mapping[str, Any]) -> tuple[int, int]:
        return int(value["uid_serial"]), int(value["uid_slot"])

    @staticmethod
    def _character_uid_key(value: Mapping[str, Any]) -> tuple[int, int]:
        return int(value["serial"]), int(value["slot"])

    def validate_bulk_plans_for_fast_apply(
        self,
        roles: list[Mapping[str, Any]],
        *,
        stable_snapshot_id: int,
    ) -> None:
        """验证一组方案可同时落地，且不会让两个角色争用同一具体装备。"""

        occupied_by: dict[tuple[int, int], str] = {}
        targets: dict[tuple[int, int], str] = {}
        validated: list[tuple[str, dict[str, Any]]] = []
        for role in roles:
            role_name = str(role["role_name"])
            plan = self.validate_plan_for_fast_apply(
                int(role["plan_id"]), stable_snapshot_id=stable_snapshot_id,
            )
            validated.append((role_name, plan))
            role_targets = [
                {
                    "character_uid": role["character_uid"],
                    "character_id": role.get("character_id"),
                },
                *[
                    row
                    for row in role.get("fallback_targets") or ()
                ],
            ]
            for target in role_targets:
                character_key = self._character_uid_key(target["character_uid"])
                previous_target = targets.setdefault(character_key, role_name)
                if previous_target != role_name:
                    raise EquipmentApplyError(
                        "极速全角色方案冲突："
                        f"[{previous_target}] 与 [{role_name}] 指向同一个角色实例"
                    )
        for role_name, plan in validated:
            for assignment in plan["assignments"]:
                uid_pair = self._uid_pair(assignment)
                previous_role = occupied_by.setdefault(uid_pair, role_name)
                if previous_role != role_name:
                    raise EquipmentApplyError(
                        "极速全角色方案冲突："
                        f"装备 UID {uid_pair} 同时被 [{previous_role}]、[{role_name}] 使用。"
                        "请先在方案中替换其中一个装备后再执行。"
                    )

    def _dispatch_with_busy_retry(
        self,
        dispatch: Callable[[], Any],
        *,
        operation: str,
        settle_seconds: float = 0.0,
        confirmed=None,
        step: str = "command",
        module_index: int = 0,
    ) -> Any:
        """只重试明确未派发的拒绝；未知或已接受结果保留原异常与执行边界。"""
        del operation
        self.dispatch_started = True
        if self.recovery is not None:
            return self.recovery.dispatch(dispatch, step=step, module_index=module_index,
                                          settle_seconds=settle_seconds, confirmed=confirmed)
        return retry_command(dispatch, check=self.execution_check,
                             step=step, settle_seconds=settle_seconds, sleeper=lambda delay: time.sleep(delay))

    def require_stable_snapshot(self) -> int:
        """固定数据库已保存的完整快照，不把刷新进度或延迟UI通知当成库存身份。"""
        state = self.sync_service.state
        if not self.sync_service.is_running:
            raise EquipmentApplyError("装配连接尚未启动，请开启同步后重试")
        snapshot_id = self.user_dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            raise EquipmentApplyError("当前账号还没有可用于极速装配的稳定背包快照")
        if state.last_snapshot_id != snapshot_id:
            # UI通知可能晚于DAO的稳定指针；固定已保存快照，不用显示竞态阻断后续装配。
            logger.warning(
                "背包同步状态快照号与数据库当前快照不同，使用数据库稳定快照继续装配："
                "state={}, database={}",
                state.last_snapshot_id,
                snapshot_id,
            )
        return snapshot_id

    def resolve_fast_apply_character_id(
        self,
        planned_character_id: int,
        snapshot_id: int,
        *,
        protagonist_target: str = "auto",
    ) -> int:
        """从冻结快照解析游戏目标ID，计算模板仍保持已保存身份。
        
        主角方案按1051模板计分，但游戏实例可能是1046；其他角色保留原正式ID。"""

        return self.resolve_fast_apply_character_ids(
            planned_character_id,
            snapshot_id,
            protagonist_target=protagonist_target,
        )[0]

    def resolve_fast_apply_character_ids(
        self,
        planned_character_id: int,
        snapshot_id: int,
        *,
        protagonist_target: str = "auto",
    ) -> tuple[int, ...]:
        """返回冻结快照中的有序目标；双主角保留女主优先、再男主的既有规则。"""

        planned_id = int(planned_character_id)
        if planned_id not in _PROTAGONIST_CHARACTER_IDS:
            return (planned_id,)

        target = str(protagonist_target or "auto").strip().lower()
        if target not in {"auto", "female", "male"}:
            target = "auto"
        present_ids = {
            character_id
            for character_id in _PROTAGONIST_CHARACTER_IDS
            if any(
                row.get("source") == "snapshot"
                and row.get("last_seen_snapshot_id") == snapshot_id
                for row in self.user_dao.list_character_instance_mappings(character_id)
            )
        }
        requested_id = (
            1051 if target == "female" else 1046 if target == "male" else None
        )
        if requested_id is not None:
            if requested_id not in present_ids:
                requested_label = "女主" if requested_id == 1051 else "男主"
                raise EquipmentApplyError(
                    f"当前稳定背包快照未包含{requested_label}主角实例 UID；"
                    "请改为自动或选择当前账号实际主角"
                )
            return (requested_id,)
        if len(present_ids) == 1:
            return (next(iter(present_ids)),)
        if len(present_ids) == 2:
            # 双实例快照未标出当前主角；保留历史女主优先规则，不在首条指令前猜测目标。
            return (1051, 1046)
        return (planned_id,)

    def resolve_character_uid(
        self,
        character_id: int,
        snapshot_id: int,
        explicit_uid: Mapping[str, Any] | None = None,
    ) -> dict[str, int]:
        """解析账号私有缓存中的角色实例 UID。

        当前稳定快照始终优先。部分 nte-core 会话会在同一账号、同一背包
        内容下间歇性漏报少量 ``characters`` 条目；角色实例 UID 本身并不会
        因这类背包事件改变，因此在当前快照缺失时允许回退到该账号此前唯一
        观察到的 snapshot 映射。装备 UID 仍必须由 *当前* 稳定快照校验，
        不能因此跨账号或使用过期背包。
        """

        if explicit_uid is not None:
            return _uid(explicit_uid, "character")

        mapped_rows = self.user_dao.list_character_instance_mappings(character_id)
        current_candidates = {
            (int(row["uid_slot"]), int(row["uid_serial"]))
            for row in mapped_rows
            if row.get("source") == "snapshot"
            and row.get("last_seen_snapshot_id") == snapshot_id
        }
        if len(current_candidates) == 1:
            slot, serial = next(iter(current_candidates))
            return {"slot": slot, "serial": serial}
        if len(current_candidates) > 1:
            raise EquipmentApplyError(
                f"当前稳定背包中角色 {character_id} 对应多个角色实例 UID"
            )

        # 手动映射仅兜底缺失的历史角色列表，不覆盖当前快照独立采集的角色UID。
        manual_candidates = {
            (row["uid_slot"], row["uid_serial"])
            for row in mapped_rows if row.get("source") == "manual"
        }
        if len(manual_candidates) == 1:
            slot, serial = next(iter(manual_candidates))
            return {"slot": slot, "serial": serial}
        if len(manual_candidates) > 1:
            raise EquipmentApplyError(
                f"角色 {character_id} 存在多个手动保存的实例 UID，请在装配前整理映射"
            )

        # nte-core 0.3.5 的角色列表在实际使用中可能发生临时缩短（例如同一
        # 账号上一份快照有 14 名角色、下一份只有 12 名）。映射表属于账号
        # 私有缓存，且角色 UID 在该账号内稳定；只有历史记录唯一时才回退，
        # 多个候选仍要求用户明确选择，避免猜测角色实例。
        cached_candidates = {
            (int(row["uid_slot"]), int(row["uid_serial"]))
            for row in mapped_rows
            if row.get("source") == "snapshot"
            and row.get("last_seen_snapshot_id") is not None
            and int(row["last_seen_snapshot_id"]) != int(snapshot_id)
        }
        if len(cached_candidates) == 1:
            slot, serial = next(iter(cached_candidates))
            logger.warning(
                "当前稳定快照未返回角色 {} 的实例 UID，"
                "回退使用该账号此前采集的唯一实例缓存（slot={}, serial={}）",
                character_id,
                slot,
                serial,
            )
            return {"slot": slot, "serial": serial}
        if len(cached_candidates) > 1:
            raise EquipmentApplyError(
                f"角色 {character_id} 在账号实例缓存中存在多个 UID，"
                "请先在一键装配中手动选择角色实例并保存映射"
            )
        raise EquipmentApplyError(
            "当前稳定背包和该账号的角色实例缓存均未包含该角色 UID；"
            "请启动背包同步并等待 nte-core 完成一次稳定快照"
        )

    @staticmethod
    def _validate_native_plan_assignments(plan: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
        """在任何RPC前排除视觉虚拟补位并校验原生方案的装备组成。"""

        # status是保存/展示状态，不是执行能力。旧incomplete驱动方案仍须逐UID校验，
        # 且只装配真实驱动，不借此改变角色当前卡带。
        assignments = list(plan.get("assignments") or ())
        modules = [item for item in assignments if item.get("kind") == "module"]
        cores = [item for item in assignments if item.get("kind") == "core"]
        for assignment in assignments:
            raw_assignment = assignment.get("raw_assignment")
            virtual = isinstance(raw_assignment, Mapping) and bool(raw_assignment.get("virtual"))
            if virtual or int(assignment.get("uid_slot") or 0) <= 0:
                raise EquipmentApplyError(
                    "方案包含虚拟补位驱动（slot=0），它不属于真实背包，不能极速装配；"
                    "请重新计算并保存完整方案"
                )
        if not 1 <= len(modules) <= 64 or len(cores) > 1:
            raise EquipmentApplyError("装配方案必须包含 1..64 个驱动，且至多包含 1 个核心")
        return modules, cores

    def validate_plan_for_fast_apply(
        self, plan_id: int, *, stable_snapshot_id: int,
    ) -> dict[str, Any]:
        """首条RPC前校验整份方案与固定库存的真实UID，避免执行一半才发现虚拟补位。"""

        snapshot_id = int(stable_snapshot_id)
        if self.user_dao.inventory_snapshot_summary(snapshot_id) is None:
            raise EquipmentApplyError("指定的稳定背包快照不存在")
        plan = self.read_plan(plan_id)
        if plan is None:
            raise EquipmentApplyError(f"装配方案 {plan_id} 不存在")
        modules, cores = self._validate_native_plan_assignments(plan)
        inventory_by_uid = {
            (item["uid_serial"], item["uid_slot"]): item
            for item in self.user_dao.list_inventory_items(snapshot_id)
        }
        for assignment in modules:
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            if inventory_by_uid.get(uid_pair, {}).get("kind") != "module":
                raise EquipmentApplyError(f"方案驱动 UID {uid_pair} 不在当前稳定背包中")
        for assignment in cores:
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            if inventory_by_uid.get(uid_pair, {}).get("kind") != "core":
                raise EquipmentApplyError(f"方案核心 UID {uid_pair} 不在当前稳定背包中")
        return plan

    def verify_plan_in_snapshot(
        self,
        plan_id: int,
        *,
        character_uid: Mapping[str, Any] | None = None,
        target_character_id: int | None = None,
        stable_snapshot_id: int,
        exact_loadout: bool = False,
        ignore_module_placement: bool = False,
    ) -> str | None:
        """只检查稳定快照，不发送任何装备或卸装指令。"""

        snapshot_id = int(stable_snapshot_id)
        plan = self.validate_plan_for_fast_apply(
            plan_id, stable_snapshot_id=snapshot_id,
        )
        effective_character_id = int(
            plan["character_id"]
            if target_character_id is None
            else target_character_id
        )
        assignments = plan["assignments"]
        modules = [item for item in assignments if item["kind"] == "module"]
        cores = [item for item in assignments if item["kind"] == "core"]
        resolved_character_uid = self.resolve_character_uid(
            effective_character_id, snapshot_id, character_uid
        )
        items = self.user_dao.list_inventory_items(snapshot_id)
        if cores or exact_loadout:
            return plan_mismatch(
                items=items,
                modules=modules,
                core_assignment=cores[0] if cores else None,
                character_id=effective_character_id,
                character_uid=resolved_character_uid,
                ignore_module_placement=ignore_module_placement,
            )
        return module_plan_mismatch(
            items=items,
            modules=modules,
            character_id=effective_character_id,
            character_uid=resolved_character_uid,
            ignore_placement=ignore_module_placement,
        )

    def plan_equipment_uid_pairs(self, plan_id: int) -> frozenset[tuple[int, int]]:
        """返回局部观测复核所需的真实装备集合，不扩大库存或读取新方案。"""

        plan = self.read_plan(int(plan_id))
        if plan is None:
            raise EquipmentApplyError("指定的装配方案不存在")
        return frozenset(
            (int(item["uid_slot"]), int(item["uid_serial"]))
            for item in plan.get("assignments") or ()
            if int(item.get("uid_slot") or 0) > 0
            and int(item.get("uid_serial") or 0) > 0
        )

    def verify_plan_in_items(
        self,
        plan_id: int,
        *,
        items: list[dict[str, Any]],
        character_uid: Mapping[str, Any],
        target_character_id: int,
        exact_loadout: bool,
        fragment_only: bool = False,
        ignore_module_placement: bool = False,
    ) -> str | None:
        """用不落库的角色局部事件复核已冻结方案，片段不冒充完整角色或格位事实。"""

        plan = self.read_plan(int(plan_id))
        if plan is None:
            raise EquipmentApplyError("指定的装配方案不存在")
        assignments = plan.get("assignments") or ()
        modules = [item for item in assignments if item.get("kind") == "module"]
        cores = [item for item in assignments if item.get("kind") == "core"]
        resolved_uid = _uid(character_uid, "character_uid")
        if fragment_only:
            return scoped_plan_mismatch(
                items=items,
                modules=modules,
                core_assignment=cores[0] if cores else None,
                character_id=int(target_character_id),
                character_uid=resolved_uid,
            )
        if cores or exact_loadout:
            return plan_mismatch(
                items=items,
                modules=modules,
                core_assignment=cores[0] if cores else None,
                character_id=int(target_character_id),
                character_uid=resolved_uid,
                ignore_module_placement=ignore_module_placement,
            )
        return module_plan_mismatch(
            items=items,
            modules=modules,
            character_id=int(target_character_id),
            character_uid=resolved_uid,
            ignore_placement=ignore_module_placement,
        )

    def apply_plan(
        self, plan_id: int, *, character_uid: Mapping[str, Any] | None = None,
        target_character_id: int | None = None, timeout: float = 30.0,
        verify_after_dispatch: bool = True, exact_loadout: bool = False,
        force_dispatch: bool = False, reset_before_apply: bool = False,
        stable_snapshot_id: int | None = None,
    ) -> EquipmentApplyResult:
        from .execution import execute_plan

        self.dispatch_started = False
        return execute_plan(
            self, plan_id, character_uid=character_uid, target_character_id=target_character_id,
            timeout=timeout, verify_after_dispatch=verify_after_dispatch, exact_loadout=exact_loadout,
            force_dispatch=force_dispatch, reset_before_apply=reset_before_apply,
            stable_snapshot_id=stable_snapshot_id,
        )
