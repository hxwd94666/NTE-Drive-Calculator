# 对 SQLite 装配方案执行一键装配，并可选择等待后续稳定快照验证结果。
"""本地核心组件一键装配的前置检查、调用和结果确认。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import time
from typing import Any, Protocol

from src.i18n import tr
from src.integrations.nte_core import is_mods_plugin_busy_error
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

from .equipment_apply_verification import (
    module_plan_mismatch,
    plan_mismatch,
    scoped_plan_mismatch,
)
from .inventory_sync_service import InventorySyncState


MAX_UID_COMPONENT = 4_294_967_295
_PROTAGONIST_CHARACTER_IDS = {1046: "male", 1051: "female"}
# nte-mods-plugin 的成功响应表示请求已被 IPC 接收；游戏线程完成装备变更
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
            raise EquipmentApplyError(tr("{field}.{component} 必须是整数", field=field, component=component))
        if raw <= 0 or raw >= MAX_UID_COMPONENT:
            raise EquipmentApplyError(
                tr("{field}.{component} 必须在 1 到 {limit} 之间",
                   field=field, component=component, limit=MAX_UID_COMPONENT - 1)
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
    ) -> None:
        self.user_dao = user_dao
        self.sync_service = sync_service

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
                        tr("极速全角色方案冲突：[{first}] 与 [{second}] 指向同一个角色实例",
                           first=previous_target, second=role_name)
                    )
        for role_name, plan in validated:
            for assignment in plan["assignments"]:
                uid_pair = self._uid_pair(assignment)
                previous_role = occupied_by.setdefault(uid_pair, role_name)
                if previous_role != role_name:
                    raise EquipmentApplyError(
                        tr("极速全角色方案冲突：装备 UID {uid} 同时被 [{first}]、[{second}] 使用。"
                           "请先在方案中替换其中一个装备后再执行。",
                           uid=uid_pair, first=previous_role, second=role_name)
                    )

    def _dispatch_with_busy_retry(
        self,
        dispatch: Callable[[], Any],
        *,
        operation: str,
        retries: int = 6,
        settle_seconds: float = 0.0,
    ) -> Any:
        """MOD 最多允许一条执行中和一条排队请求，忙时串行重试。"""

        for attempt in range(retries):
            try:
                result = dispatch()
                if settle_seconds > 0:
                    # 游戏内没有操作后的背包快照可等；留出一个完整执行间隔，
                    # 避免下一条 RPC 抢占 MOD 的执行位或排队位。
                    time.sleep(settle_seconds)
                return result
            except Exception as exc:
                if not is_mods_plugin_busy_error(exc) or attempt + 1 >= retries:
                    raise
                logger.warning("{} 遇到装备 MOD 队列繁忙，正在重试：{}", operation, exc)
                time.sleep(0.6)
        raise EquipmentApplyError(tr("{operation} 未能提交到装备 MOD", operation=operation))

    def require_stable_snapshot(self) -> int:
        """Validate the live sync once and return the snapshot to pin for a job."""
        state = self.sync_service.state
        if not self.sync_service.is_running or state.phase not in {"listening", "collecting"}:
            raise EquipmentApplyError(tr("背包同步必须处于稳定监听状态才能一键装配"))
        snapshot_id = self.user_dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            raise EquipmentApplyError(tr("当前账号还没有可用于极速装配的稳定背包快照"))
        if state.last_snapshot_id != snapshot_id:
            # A guarded residual event may temporarily keep the UI state on
            # its earlier notification while the immutable current snapshot
            # remains usable.  Pin that SQLite snapshot rather than blocking
            # a follow-up single-role apply on a display-state race.
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
        """Resolve the live target ID without changing the calculation template.

        The saved protagonist plan deliberately uses the female static template
        (1051) for scoring.  The game-side character instance, however, can be
        male (1046).  Pick that live instance from the *pinned* snapshot before
        dispatching the plugin command; other roles retain their saved ID.
        """

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
        """Return ordered live targets; a dual protagonist tries female then male."""

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
                requested_label = tr("女主") if requested_id == 1051 else tr("男主")
                raise EquipmentApplyError(
                    tr("当前稳定背包快照未包含{label}主角实例 UID；请改为自动或选择当前账号实际主角",
                       label=requested_label)
                )
            return (requested_id,)
        if len(present_ids) == 1:
            return (next(iter(present_ids)),)
        if len(present_ids) == 2:
            # Compatibility default: historical plans and all score templates
            # use the female variant.  A dual-instance snapshot has no active
            # avatar marker, so retry the male instance only if female dispatch
            # fails instead of guessing before the first plugin call.
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
                tr("当前稳定背包中角色 {role} 对应多个角色实例 UID", role=character_id)
            )

        # A manual selection remains useful only for old snapshots collected
        # before nte-core exposed `characters`; it never overrides a current
        # independently captured character UID.
        manual_candidates = {
            (row["uid_slot"], row["uid_serial"])
            for row in mapped_rows if row.get("source") == "manual"
        }
        if len(manual_candidates) == 1:
            slot, serial = next(iter(manual_candidates))
            return {"slot": slot, "serial": serial}
        if len(manual_candidates) > 1:
            raise EquipmentApplyError(
                tr("角色 {role} 存在多个手动保存的实例 UID，请在装配前整理映射", role=character_id)
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
                tr("角色 {role} 在账号实例缓存中存在多个 UID，"
                   "请先在一键装配中手动选择角色实例并保存映射", role=character_id)
            )
        raise EquipmentApplyError(
            tr("当前稳定背包和该账号的角色实例缓存均未包含该角色 UID；"
               "请启动背包同步并等待 nte-core 完成一次稳定快照")
        )

    @staticmethod
    def _validate_native_plan_assignments(plan: Mapping[str, Any]) -> tuple[list[dict], list[dict]]:
        """Reject display-only/partial plans before any equipment RPC is sent."""

        # ``status`` records how a plan was saved and displayed; it is not an
        # execution capability flag.  Older driver-only plans were persisted
        # as ``incomplete`` even though nte-core can apply their real modules
        # without changing the character's current core.
        assignments = list(plan.get("assignments") or ())
        modules = [item for item in assignments if item.get("kind") == "module"]
        cores = [item for item in assignments if item.get("kind") == "core"]
        for assignment in assignments:
            raw_assignment = assignment.get("raw_assignment")
            virtual = isinstance(raw_assignment, Mapping) and bool(raw_assignment.get("virtual"))
            if virtual or int(assignment.get("uid_slot") or 0) <= 0:
                raise EquipmentApplyError(
                    tr("方案包含虚拟补位驱动（slot=0），它不属于真实背包，不能极速装配；"
                       "请重新计算并保存完整方案")
                )
        if not 1 <= len(modules) <= 64 or len(cores) > 1:
            raise EquipmentApplyError(tr("装配方案必须包含 1..64 个驱动，且至多包含 1 个核心"))
        return modules, cores

    def validate_plan_for_fast_apply(
        self, plan_id: int, *, stable_snapshot_id: int,
    ) -> dict[str, Any]:
        """Check every native UID before creating or resuming an apply job.

        This deliberately validates the whole plan before the first RPC.  A
        historical incomplete plan may contain visual virtual placeholders;
        discovering one after previous roles were dispatched leaves a job only
        partially applied.
        """

        snapshot_id = int(stable_snapshot_id)
        if self.user_dao.inventory_snapshot_summary(snapshot_id) is None:
            raise EquipmentApplyError(tr("指定的稳定背包快照不存在"))
        plan = self.user_dao.get_loadout_plan(plan_id)
        if plan is None:
            raise EquipmentApplyError(tr("装配方案 {plan} 不存在", plan=plan_id))
        modules, cores = self._validate_native_plan_assignments(plan)
        inventory_by_uid = {
            (item["uid_serial"], item["uid_slot"]): item
            for item in self.user_dao.list_inventory_items(snapshot_id)
        }
        for assignment in modules:
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            if inventory_by_uid.get(uid_pair, {}).get("kind") != "module":
                raise EquipmentApplyError(tr("方案驱动 UID {uid} 不在当前稳定背包中", uid=uid_pair))
        for assignment in cores:
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            if inventory_by_uid.get(uid_pair, {}).get("kind") != "core":
                raise EquipmentApplyError(tr("方案核心 UID {uid} 不在当前稳定背包中", uid=uid_pair))
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
        """Return the real items required for an in-memory scoped verification."""

        plan = self.user_dao.get_loadout_plan(int(plan_id))
        if plan is None:
            raise EquipmentApplyError(tr("指定的装配方案不存在"))
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
    ) -> str | None:
        """Verify one saved plan against a non-persisted role-scoped event."""

        plan = self.user_dao.get_loadout_plan(int(plan_id))
        if plan is None:
            raise EquipmentApplyError(tr("指定的装配方案不存在"))
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
            )
        return module_plan_mismatch(
            items=items,
            modules=modules,
            character_id=int(target_character_id),
            character_uid=resolved_uid,
        )

    def apply_plan(
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
            raise EquipmentApplyError(tr("当前 nte-core 不支持 equipment 能力"))
        if stable_snapshot_id is None:
            before_snapshot_id = self.require_stable_snapshot()
        else:
            before_snapshot_id = int(stable_snapshot_id)
            if self.user_dao.inventory_snapshot_summary(before_snapshot_id) is None:
                raise EquipmentApplyError(tr("指定的稳定背包快照不存在"))

        plan = self.validate_plan_for_fast_apply(
            plan_id, stable_snapshot_id=before_snapshot_id,
        )
        effective_character_id = int(
            plan["character_id"]
            if target_character_id is None
            else target_character_id
        )
        assignments = plan["assignments"]
        modules = [item for item in assignments if item["kind"] == "module"]
        cores = [item for item in assignments if item["kind"] == "core"]

        current_items = self.user_dao.list_inventory_items(before_snapshot_id)
        by_uid = {
            (item["uid_serial"], item["uid_slot"]): item for item in current_items
        }
        selected_uids: set[tuple[int, int]] = set()
        placements: list[dict[str, Any]] = []
        for index, assignment in enumerate(modules):
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            if uid_pair in selected_uids:
                raise EquipmentApplyError(tr("方案中存在重复装备 UID"))
            selected_uids.add(uid_pair)
            item = by_uid.get(uid_pair)
            if item is None or item["kind"] != "module":
                raise EquipmentApplyError(tr("方案驱动 UID {uid} 不在当前稳定背包中", uid=uid_pair))
            if assignment.get("rotation") not in (None, 0):
                raise EquipmentApplyError(tr("nte-core 一键装配不接受旋转参数"))
            row = assignment.get("target_row")
            column = assignment.get("target_column")
            if row not in range(1, 6) or column not in range(1, 6):
                raise EquipmentApplyError(tr("第 {index} 个驱动位置必须在 1..5", index=index + 1))
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
                raise EquipmentApplyError(tr("方案中存在重复装备 UID"))
            core_item = by_uid.get(core_pair)
            if core_item is None or core_item["kind"] != "core":
                raise EquipmentApplyError(tr("方案核心 UID {uid} 不在当前稳定背包中", uid=core_pair))
            if core_assignment.get("rotation") not in (None, 0):
                raise EquipmentApplyError(tr("核心不能包含旋转参数"))

        resolved_character_uid = self.resolve_character_uid(
            effective_character_id, before_snapshot_id, character_uid
        )
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
        # A normal read-only apply may skip a plan already present in the
        # frozen snapshot.  Full-reset apply is deliberately different: every
        # requested role must first be cleared, even if that snapshot happens
        # to describe the target layout as already present.
        if current_mismatch is None and not force_dispatch and not reset_before_apply:
            return EquipmentApplyResult(
                plan_id=plan["plan_id"],
                before_snapshot_id=before_snapshot_id,
                after_snapshot_id=before_snapshot_id,
                character_uid=resolved_character_uid,
                rpc_result={"status": "already_applied"},
                already_applied=True,
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
                lambda: self.sync_service.unequip_all(
                    character=resolved_character_uid
                ),
                operation=tr("卸下角色现有装备"),
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
                operation=tr("一键装配"),
                settle_seconds=(
                    0.0
                    if verify_after_dispatch
                    else FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS
                ),
            )
            if not verify_after_dispatch:
                return EquipmentApplyResult(
                    plan_id=plan["plan_id"],
                    before_snapshot_id=before_snapshot_id,
                    after_snapshot_id=before_snapshot_id,
                    character_uid=resolved_character_uid,
                    rpc_result=rpc_result,
                    verified=False,
                )
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
                    source_item["equipped"]
                    and source_item.get("equipped_character_uid")
                    == resolved_character_uid
                )
                move_existing = bool(
                    source_item["equipped"]
                    and not (reset_target and source_is_reset_target)
                )
                dispatcher = (
                    self.sync_service.move_module_to_character
                    if move_existing
                    else self.sync_service.equip_module
                )
                dispatch_name = tr("移动已装备驱动") if move_existing else tr("装备驱动")
                if verify_after_dispatch:
                    rpc_item_result = dispatcher(
                        character=resolved_character_uid,
                        equipment=placement["equipment"],
                        row=placement["row"],
                        column=placement["column"],
                    )
                else:
                    rpc_item_result = self._dispatch_with_busy_retry(
                        lambda: dispatcher(
                            character=resolved_character_uid,
                            equipment=placement["equipment"],
                            row=placement["row"],
                            column=placement["column"],
                        ),
                        operation=dispatch_name,
                        settle_seconds=FAST_EQUIPMENT_COMMAND_SETTLE_SECONDS,
                    )
                rpc_result.append(rpc_item_result)
                logger.info(
                    "角色 {} 驱动 {}/{} 已串行下发：UID ({}, {}) → ({}, {})，方式={}",
                    effective_character_id,
                    len(rpc_result),
                    len(modules),
                    assignment["uid_serial"],
                    assignment["uid_slot"],
                    placement["row"],
                    placement["column"],
                    dispatch_name,
                )
                if not verify_after_dispatch:
                    continue
                # The plugin permits only one active and one queued request.
                # Wait after every module so driver-only plans cannot overfill
                # that queue when they contain several placements.
                after_state = self.sync_service.wait_for_snapshot(
                    after_snapshot_id=after_snapshot_id,
                    timeout=timeout,
                )
                after_snapshot_id = after_state.last_snapshot_id
            if not verify_after_dispatch:
                return EquipmentApplyResult(
                    plan_id=plan["plan_id"],
                    before_snapshot_id=before_snapshot_id,
                    after_snapshot_id=before_snapshot_id,
                    character_uid=resolved_character_uid,
                    rpc_result=rpc_result,
                    verified=False,
                )
        if after_snapshot_id is None or after_snapshot_id <= before_snapshot_id:
            raise EquipmentApplyError(tr("核心组件没有返回装配后的新稳定快照"))

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
            raise EquipmentApplyError(tr("新快照未确认目标配装：{mismatch}", mismatch=mismatch))
        return EquipmentApplyResult(
            plan_id=plan["plan_id"],
            before_snapshot_id=before_snapshot_id,
            after_snapshot_id=after_snapshot_id,
            character_uid=resolved_character_uid,
            rpc_result=rpc_result,
        )
