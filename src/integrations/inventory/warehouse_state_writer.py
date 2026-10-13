# 通过已运行的 nte-core 会话写回仓库装备的锁定与弃置状态。
"""nte-core adapter for warehouse equipment state changes."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass
from time import perf_counter, sleep
from typing import Any, Protocol

from src.integrations.nte_core_protocol import NteCoreRpcError, is_mods_plugin_busy_error
from src.integrations.nte_core_equipment import MAX_STATE_OPERATIONS, STATE_BATCH_CAPABILITY


_TRANSIENT_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64)


@dataclass(frozen=True)
class EquipmentStateBatchMetrics:
    group_count: int
    rpc_count: int
    wall_duration_ms: float
    rpc_duration_ms: float
    retry_count: int


class WarehouseStateWriteError(RuntimeError):
    """The live core session cannot safely accept a warehouse state change."""


class LiveInventorySync(Protocol):
    def set_item_states(self, *, operations: list[Mapping[str, Any]]) -> Any: ...
    def equipment_batch(self) -> AbstractContextManager: ...

    @property
    def state(self) -> Any: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def core_hello_result(self) -> dict[str, Any] | None: ...

    def set_item_discarded(
        self,
        *,
        equipment: Mapping[str, Any],
        discarded: bool,
    ) -> Any: ...

    def set_item_locked(
        self,
        *,
        equipment: Mapping[str, Any],
        locked: bool,
    ) -> Any: ...

    def wait_for_snapshot(
        self,
        *,
        after_snapshot_id: int | None = None,
        timeout: float = 30.0,
    ) -> Any: ...

    def begin_full_inventory_guard(
        self,
        item_uids: frozenset[tuple[int, int]],
        *,
        source_snapshot_id: int | None = None,
    ) -> object: ...

    def end_full_inventory_guard(self, token: object) -> None: ...

    def finish_full_inventory_guard(
        self,
        token: object,
        *,
        grace_seconds: float,
    ) -> bool: ...


class WarehouseStateWriter:
    """Validate and send state RPCs without owning snapshot or UI policy."""

    def __init__(self, sync_service: LiveInventorySync) -> None:
        self.sync_service = sync_service

    @contextmanager
    def batch(self):
        with ExitStack() as scope:
            dispatcher = scope.enter_context(self.sync_service.equipment_batch())
            yield dispatcher

    @staticmethod
    def _operations(
        row: Mapping[str, Any],
        target_state: str,
        equipment: Mapping[str, int],
    ) -> tuple[dict[str, Any], ...]:
        if target_state not in {"normal", "locked", "discarded"}:
            raise WarehouseStateWriteError(f"未知目标状态：{target_state}")
        discarded = bool(row.get("discarded"))
        locked = bool(row.get("locked"))
        operations: list[dict[str, Any]] = []

        def add(method: str, state_name: str, value: bool) -> None:
            operations.append({
                "method": method,
                "kwargs": {"equipment": dict(equipment), state_name: value},
            })

        if target_state == "normal":
            if discarded:
                add("set_item_discarded", "discarded", False)
            if locked:
                add("set_item_locked", "locked", False)
        elif target_state == "locked":
            if discarded:
                add("set_item_discarded", "discarded", False)
            if not locked:
                add("set_item_locked", "locked", True)
        else:
            if locked:
                add("set_item_locked", "locked", False)
            if not discarded:
                add("set_item_discarded", "discarded", True)
        return tuple(operations)

    def apply_many(
        self,
        changes: list[tuple[Mapping[str, Any], str, Mapping[str, int]]],
        *,
        group_completed=None,
    ) -> EquipmentStateBatchMetrics:
        groups = tuple(
            self._operations(row, target_state, equipment)
            for row, target_state, equipment in changes
        )
        capabilities = (self.sync_service.core_hello_result or {}).get("capabilities", [])
        if isinstance(capabilities, list) and STATE_BATCH_CAPABILITY in capabilities:
            return self._apply_state_batches(groups, group_completed)
        with self.batch():
            started = perf_counter()
            rpc_count = 0
            rpc_duration = 0.0
            retry_count = 0
            for index, group in enumerate(groups, 1):
                for operation in group:
                    elapsed, attempts, retries = self._dispatch_operation(operation)
                    rpc_duration += elapsed
                    rpc_count += attempts
                    retry_count += retries
                if group_completed is not None:
                    group_completed(index, len(groups))
            return EquipmentStateBatchMetrics(
                group_count=len(groups),
                rpc_count=rpc_count,
                wall_duration_ms=round((perf_counter() - started) * 1000.0, 3),
                rpc_duration_ms=round(rpc_duration * 1000.0, 3),
                retry_count=retry_count,
            )

    def _apply_state_batches(self, groups, group_completed):
        started = perf_counter()
        rpc_duration = 0.0
        rpc_count = retry_count = 0
        pending = []
        completed_groups = 0
        with self.batch():
            for index, group in enumerate(groups, 1):
                # Keep unlock/discard pairs together, including at chunk boundaries.
                if pending and len(pending) + len(group) > MAX_STATE_OPERATIONS:
                    elapsed, attempts, retries = self._dispatch_operation(
                        {"method": "set_item_states", "kwargs": {"operations": pending}})
                    rpc_duration += elapsed
                    rpc_count += attempts
                    retry_count += retries
                    pending = []
                    if group_completed is not None:
                        group_completed(completed_groups, len(groups))
                for operation in group:
                    kwargs = operation["kwargs"]
                    field = "locked" if operation["method"] == "set_item_locked" else "discarded"
                    pending.append({"equipment": kwargs["equipment"], "field": field, "value": kwargs[field]})
                completed_groups = index
            if pending:
                elapsed, attempts, retries = self._dispatch_operation(
                    {"method": "set_item_states", "kwargs": {"operations": pending}})
                rpc_duration += elapsed
                rpc_count += attempts
                retry_count += retries
            if group_completed is not None:
                group_completed(len(groups), len(groups))
        return EquipmentStateBatchMetrics(len(groups), rpc_count,
            round((perf_counter() - started) * 1000, 3), round(rpc_duration * 1000, 3), retry_count)

    @staticmethod
    def _is_pre_dispatch_transient(error: BaseException) -> bool:
        return is_mods_plugin_busy_error(error) or (
            isinstance(error, NteCoreRpcError)
            and error.code == -32001
            and error.message == "source_changed"
            and error.domain_code is None
        )

    def _dispatch_operation(self, operation: Mapping[str, Any]) -> tuple[float, int, int]:
        started = perf_counter()
        attempts = 0
        retries = 0
        while True:
            attempts += 1
            try:
                getattr(self.sync_service, operation["method"])(**operation["kwargs"])
                return perf_counter() - started, attempts, retries
            except Exception as error:
                if not self._is_pre_dispatch_transient(error):
                    raise
                if retries >= len(_TRANSIENT_RETRY_DELAYS):
                    raise WarehouseStateWriteError(
                        "组件状态持续刷新，当前指令多次未派发；已停止本批次，等待背包刷新后可继续处理剩余装备"
                    ) from error
                sleep(_TRANSIENT_RETRY_DELAYS[retries])
                retries += 1

    def ensure_ready(self) -> None:
        state = self.sync_service.state
        if (
            not self.sync_service.is_running
            or getattr(state, "phase", None) != "listening"
        ):
            raise WarehouseStateWriteError(
                "背包同步必须处于稳定监听状态才能管理仓库"
            )
        capabilities = (self.sync_service.core_hello_result or {}).get(
            "capabilities",
            [],
        )
        if not isinstance(capabilities, list) or "equipment" not in capabilities:
            raise WarehouseStateWriteError(
                "当前 nte-core 不支持 equipment 状态管理能力"
            )

    def apply_one(
        self,
        row: Mapping[str, Any],
        target_state: str,
        equipment: Mapping[str, int],
    ) -> None:
        for operation in self._operations(row, target_state, equipment):
            self._dispatch_operation(operation)
