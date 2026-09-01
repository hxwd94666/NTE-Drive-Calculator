# 在应用生命周期内持续接收、稳定并保存本地核心组件的背包快照。
"""在应用生命周期内持续接收、稳定并保存 nte-core 背包快照。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.i18n import tr
from src.integrations.nte_core import NteCoreClient
from src.observability import OperationContext, log_event
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

from .inventory_sync_contracts import InventoryCoreClient
from .inventory_sync_runtime import prune_raw_captures, run_inventory_sync


SyncPhase = Literal[
    "stopped",
    "starting",
    "waiting",
    "collecting",
    "saving",
    "listening",
    "error",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class InventorySyncState:
    phase: SyncPhase = "stopped"
    message: str = "背包同步尚未启动"
    running: bool = False
    capturing: bool = False
    pending_item_count: int | None = None
    added_count: int = 0
    removed_count: int = 0
    last_snapshot_id: int | None = None
    last_item_count: int | None = None
    last_synced_at_utc: str | None = None
    error: str | None = None
    error_code: str | None = None
    updated_at_utc: str = ""


@dataclass(frozen=True)
class ScopedEquipmentSnapshot:
    """一条仅用于装配复核、绝不写入 SQLite 的角色局部库存事件。"""

    cursor: int
    items: tuple[dict[str, Any], ...]
    uid_pairs: frozenset[tuple[int, int]]
    event_uid_pairs: frozenset[tuple[int, int]] = frozenset()
    event_message: dict[str, Any] | None = None


StateHandler = Callable[[InventorySyncState], None]


def _default_core_client() -> InventoryCoreClient:
    return NteCoreClient()


class InventorySyncService:
    """后台同步服务。

    nte-core 回调只替换内存中的最新事件并唤醒工作线程，不执行 SQLite 写入；因此
    大背包和连续事件不会堵塞核心组件的事件分发线程。
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        account_id: str | None = None,
        account_name: str | None = None,
        client_factory: Callable[[], InventoryCoreClient] = _default_core_client,
        dao_factory: Callable[..., UserDataDao] = UserDataDao,
        settle_seconds: float | None = None,
        capture_device_id: str | None = None,
        raw_capture_enabled: bool | None = None,
        raw_capture_directory: str | Path | None = None,
        poll_seconds: float = 0.05,
        template_refresh: Callable[[], Any] | None = None,
        operation_context: OperationContext | None = None,
    ) -> None:
        if settle_seconds is not None and settle_seconds <= 0:
            raise ValueError("settle_seconds 必须大于 0")
        if poll_seconds <= 0:
            raise ValueError("poll_seconds 必须大于 0")
        self.database_path = Path(database_path).expanduser().resolve()
        self.account_id = account_id
        self.account_name = account_name
        self._client_factory = client_factory
        self._dao_factory = dao_factory
        self._settle_seconds = settle_seconds
        self._capture_device_id = capture_device_id
        self._raw_capture_enabled = raw_capture_enabled
        self._raw_capture_directory = (
            Path(raw_capture_directory).expanduser().resolve()
            if raw_capture_directory is not None
            else None
        )
        self._poll_seconds = poll_seconds
        self._template_refresh = template_refresh
        self._operation_context = operation_context or OperationContext.create(
            "inventory_sync",
            account_id=account_id,
        )

        self._state = InventorySyncState(updated_at_utc=_utc_now())
        self._state_condition = threading.Condition()
        self._handlers: list[StateHandler] = []
        self._handlers_lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._latest_inventory_event: dict[str, Any] | None = None
        self._event_ready = threading.Event()
        self._snapshot_guard_lock = threading.Lock()
        self._snapshot_guard_token: object | None = None
        self._snapshot_guard_uids: frozenset[tuple[int, int]] | None = None
        self._snapshot_guard_source_snapshot_id: int | None = None
        self._snapshot_guard_expires_at: float | None = None
        self._snapshot_guard_generation = 0
        self._snapshot_guard_packet_count = 0
        self._snapshot_guard_valid_item_count = 0
        self._snapshot_guard_known_item_count = 0
        self._snapshot_guard_saw_full_uid_set = False
        self._snapshot_guard_saw_declared_reduction = False
        self._scoped_snapshot_cursor = 0
        self._scoped_equipment_snapshots: list[ScopedEquipmentSnapshot] = []
        self._pending_runtime_state_deltas: list[tuple[int, tuple[dict[str, Any], ...], int | None, int | None]] = []
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: InventoryCoreClient | None = None

    @property
    def state(self) -> InventorySyncState:
        with self._state_condition:
            return self._state

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def core_hello_result(self) -> dict[str, Any] | None:
        """返回握手能力的副本，供装配等上层服务做调用前检查。"""

        client = self._client
        if client is None or client.hello_result is None:
            return None
        return dict(client.hello_result)

    def begin_full_inventory_guard(
        self,
        item_uids: frozenset[tuple[int, int]],
        *,
        source_snapshot_id: int | None = None,
    ) -> object:
        """Require the next fast-apply snapshots to retain this full UID set.

        Equipment state can legitimately change during a bulk apply, whereas
        the physical inventory UID set cannot.  This protects the current
        snapshot pointer from scoped nte-core responses that contain only the
        character currently being equipped.
        """

        if not item_uids:
            raise ValueError("极速装配的冻结库存不包含可校验装备")
        token = object()
        with self._snapshot_guard_lock:
            if self._snapshot_guard_token is not None:
                if self._snapshot_guard_expires_at is None:
                    raise RuntimeError("已有极速装配正在保护背包快照")
                # The previous task has returned to the UI and is only
                # retaining a grace guard for late residual packets.  A user
                # may start the next task immediately; hand the guard over to
                # it instead of forcing an arbitrary 90-second wait.  The old
                # token can no longer release this replacement guard.
            self._snapshot_guard_token = token
            self._snapshot_guard_uids = frozenset(item_uids)
            self._snapshot_guard_source_snapshot_id = source_snapshot_id
            self._snapshot_guard_expires_at = None
            self._snapshot_guard_generation += 1
            self._snapshot_guard_packet_count = 0
            self._snapshot_guard_valid_item_count = 0
            self._snapshot_guard_known_item_count = 0
            self._snapshot_guard_saw_full_uid_set = False
            self._snapshot_guard_saw_declared_reduction = False
        with self._state_condition:
            self._scoped_equipment_snapshots.clear()
            self._pending_runtime_state_deltas.clear()
            self._state_condition.notify_all()
        return token

    def end_full_inventory_guard(self, token: object) -> None:
        """Release a previously installed fast-apply full-inventory guard."""

        diagnostic: tuple[int, int, int, bool] | None = None
        with self._snapshot_guard_lock:
            if token is not self._snapshot_guard_token:
                return
            diagnostic = self._snapshot_guard_diagnostic_locked()
            self._snapshot_guard_token = None
            self._snapshot_guard_uids = None
            self._snapshot_guard_source_snapshot_id = None
            self._snapshot_guard_expires_at = None
            self._snapshot_guard_generation += 1
        self._log_guard_diagnostic(diagnostic)
        with self._state_condition:
            self._scoped_equipment_snapshots.clear()
            self._pending_runtime_state_deltas.clear()
            self._state_condition.notify_all()

    def finish_full_inventory_guard(self, token: object, *, grace_seconds: float) -> bool:
        """Keep the membership filter through delayed apply responses.

        A fast equipment request may be followed by per-character inventory
        responses long after the UI task has returned.  They update only the
        matching runtime state overlay, never the complete snapshot pointer.
        The filter releases only when the same full frozen UID set reappears,
        or when a later fast-apply task takes ownership of it.
        """

        del grace_seconds
        with self._snapshot_guard_lock:
            if token is not self._snapshot_guard_token:
                return False
            self._snapshot_guard_expires_at = float("inf")
        return True

    def guard_observed_inventory_reduction(self, token: object) -> bool:
        """Return whether this guarded action received a smaller declared count."""

        with self._snapshot_guard_lock:
            return bool(
                token is self._snapshot_guard_token
                and self._snapshot_guard_saw_declared_reduction
            )

    def _full_inventory_guard(self) -> tuple[int, frozenset[tuple[int, int]] | None]:
        with self._snapshot_guard_lock:
            expires_at = self._snapshot_guard_expires_at
            if expires_at is not None and time.monotonic() >= expires_at:
                self._snapshot_guard_token = None
                self._snapshot_guard_uids = None
                self._snapshot_guard_source_snapshot_id = None
                self._snapshot_guard_expires_at = None
                self._snapshot_guard_generation += 1
            return self._snapshot_guard_generation, self._snapshot_guard_uids

    def _full_inventory_guard_source_snapshot_id(self) -> int | None:
        with self._snapshot_guard_lock:
            return self._snapshot_guard_source_snapshot_id

    def _release_finished_guard_for_full_snapshot(
        self,
        expected_uids: frozenset[tuple[int, int]],
    ) -> None:
        """Release only a finished guard once its full frozen inventory returns."""

        diagnostic: tuple[int, int, int, bool] | None = None
        with self._snapshot_guard_lock:
            if (
                self._snapshot_guard_expires_at is None
                or self._snapshot_guard_uids != expected_uids
            ):
                return
            diagnostic = self._snapshot_guard_diagnostic_locked()
            self._snapshot_guard_token = None
            self._snapshot_guard_uids = None
            self._snapshot_guard_source_snapshot_id = None
            self._snapshot_guard_expires_at = None
            self._snapshot_guard_generation += 1
        self._log_guard_diagnostic(diagnostic)

    def _snapshot_guard_diagnostic_locked(self) -> tuple[int, int, int, bool]:
        return (
            self._snapshot_guard_packet_count,
            self._snapshot_guard_valid_item_count,
            self._snapshot_guard_known_item_count,
            self._snapshot_guard_saw_full_uid_set,
        )

    @staticmethod
    def _log_guard_diagnostic(diagnostic: tuple[int, int, int, bool] | None) -> None:
        if diagnostic is None:
            return
        logger.info(
            "快照守卫测试结果：收到背包事件={}，事件内有效装备={}，命中冻结库存={}，完整UID集合={}",
            *diagnostic,
        )

    def equip_one_key(
        self,
        *,
        character: Mapping[str, Any],
        placements: list[Mapping[str, Any]],
        core: Mapping[str, Any],
        timeout: float | None = None,
    ) -> Any:
        """复用正在持续抓取的核心进程执行一键装配。"""

        client = self._client
        if client is None or not self.is_running:
            raise RuntimeError("背包同步服务未运行，不能调用一键装配")
        return client.equip_one_key(
            character=character,
            placements=placements,
            core=core,
            timeout=timeout,
        )

    def equip_module(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
        row: int,
        column: int,
    ) -> Any:
        """装配一个未装备驱动，用于没有卡带的驱动-only 方案。"""

        client = self._equipment_client()
        return client.equip_module(
            character=character, equipment=equipment, row=row, column=column,
        )

    def unequip_module(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> Any:
        """卸下指定驱动，用于释放被其他非目标角色占用的方案装备。"""

        client = self._equipment_client()
        return client.unequip_module(character=character, equipment=equipment)

    def unequip_core(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> Any:
        """卸下指定卡带，用于释放被其他非目标角色占用的方案装备。"""

        client = self._equipment_client()
        return client.unequip_core(character=character, equipment=equipment)

    def unequip_all(self, *, character: Mapping[str, Any]) -> Any:
        """卸下角色当前全部驱动和卡带。"""

        client = self._equipment_client()
        return client.unequip_all(character=character)

    def move_module_to_character(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
        row: int,
        column: int,
    ) -> Any:
        """移动已装备驱动，用于没有卡带的驱动-only 方案。"""

        client = self._equipment_client()
        return client.move_module_to_character(
            character=character, equipment=equipment, row=row, column=column,
        )

    def set_item_discarded(self, *, equipment: Mapping[str, Any], discarded: bool) -> Any:
        """复用持续运行的核心进程更新单件装备的弃置状态。"""
        client = self._equipment_client()
        return client.set_item_discarded(equipment=equipment, discarded=discarded)

    def set_item_locked(self, *, equipment: Mapping[str, Any], locked: bool) -> Any:
        """复用持续运行的核心进程更新单件装备的锁定状态。"""
        client = self._equipment_client()
        return client.set_item_locked(equipment=equipment, locked=locked)

    def _equipment_client(self) -> InventoryCoreClient:
        client = self._client
        if client is None or not self.is_running:
            raise RuntimeError("背包同步服务未运行，不能修改装备状态")
        hello = self.core_hello_result or {}
        capabilities = hello.get("capabilities", [])
        if not isinstance(capabilities, list) or "equipment" not in capabilities:
            raise RuntimeError("当前 nte-core 不支持 equipment 状态管理能力")
        return client

    def add_state_handler(self, handler: StateHandler) -> None:
        with self._handlers_lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def remove_state_handler(self, handler: StateHandler) -> None:
        with self._handlers_lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def _publish(self, phase: SyncPhase, message: str, **changes: Any) -> None:
        with self._state_condition:
            self._state = replace(
                self._state,
                phase=phase,
                message=message,
                updated_at_utc=_utc_now(),
                **changes,
            )
            state = self._state
            self._state_condition.notify_all()
        with self._handlers_lock:
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                handler(state)
            except Exception:
                # 界面观察者不能终止背包同步线程。
                continue

    def start(self) -> None:
        if self.is_running:
            return
        log_event(
            "INFO",
            "inventory_sync.started",
            "启动背包同步服务",
            self._operation_context,
        )
        self._stop_requested.clear()
        self._event_ready.clear()
        with self._event_lock:
            self._latest_inventory_event = None
        self._publish(
            "starting",
            tr("正在启动背包同步服务"),
            running=True,
            capturing=False,
            error=None,
            error_code=None,
        )
        self._thread = threading.Thread(
            target=self._run,
            name="inventory-sync-service",
            daemon=True,
        )
        self._thread.start()

    def _on_inventory_event(self, event: dict[str, Any]) -> None:
        # 单槽合并：完整快照描述的是某一时刻的全部背包，积压时只需处理最新版本。
        self._capture_scoped_equipment_snapshot(event)
        with self._event_lock:
            self._latest_inventory_event = dict(event)
        self._event_ready.set()

    def scoped_equipment_snapshot_cursor(self) -> int:
        """Return the in-memory cursor used to fence one equipment dispatch."""

        with self._state_condition:
            return self._scoped_snapshot_cursor

    def wait_for_equipment_snapshot(
        self,
        required_uids: frozenset[tuple[int, int]],
        *,
        after_cursor: int,
        timeout: float = 30.0,
    ) -> ScopedEquipmentSnapshot:
        """Wait for a post-dispatch partial event containing one role's items.

        This path is intentionally separate from ``wait_for_snapshot``: the
        returned rows remain an in-memory verification input and never replace
        the account's complete current inventory snapshot.
        """

        expected = frozenset((int(slot), int(serial)) for slot, serial in required_uids)
        if not expected:
            raise ValueError("角色局部复核至少需要一件装备 UID")
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._state_condition:
            while True:
                fragments = [
                    snapshot
                    for snapshot in self._scoped_equipment_snapshots
                    if snapshot.cursor > int(after_cursor)
                ]
                merged: dict[tuple[int, int], dict[str, Any]] = {}
                for snapshot in fragments:
                    for item in snapshot.items:
                        merged[(int(item["uid"]["slot"]), int(item["uid"]["serial"]))] = item
                if expected.issubset(merged):
                    return ScopedEquipmentSnapshot(
                        cursor=max(snapshot.cursor for snapshot in fragments),
                        items=tuple(merged[key] for key in sorted(merged)),
                        uid_pairs=frozenset(merged),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待角色装配局部快照超时")
                self._state_condition.wait(remaining)

    def wait_for_observed_equipment_snapshot(
        self,
        required_uids: frozenset[tuple[int, int]],
        *,
        after_cursor: int,
        timeout: float = 30.0,
    ) -> ScopedEquipmentSnapshot:
        """Return cached target rows as soon as any post-dispatch target appears."""

        expected = frozenset((int(slot), int(serial)) for slot, serial in required_uids)
        if not expected:
            raise ValueError("角色局部复核至少需要一件装备 UID")
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._state_condition:
            while True:
                fragments = [
                    snapshot
                    for snapshot in self._scoped_equipment_snapshots
                    if snapshot.cursor > int(after_cursor)
                ]
                merged: dict[tuple[int, int], dict[str, Any]] = {}
                for snapshot in fragments:
                    for item in snapshot.items:
                        pair = (int(item["uid"]["slot"]), int(item["uid"]["serial"]))
                        if pair in expected:
                            merged[pair] = item
                if merged:
                    return ScopedEquipmentSnapshot(
                        cursor=max(snapshot.cursor for snapshot in fragments),
                        items=tuple(merged[key] for key in sorted(merged)),
                        uid_pairs=frozenset(merged),
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待角色装配局部快照超时")
                self._state_condition.wait(remaining)

    def wait_for_action_inventory_snapshot(
        self,
        required_uids: frozenset[tuple[int, int]],
        *,
        after_cursor: int,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Return one action-scoped packet that contains every requested UID."""

        expected = frozenset((int(slot), int(serial)) for slot, serial in required_uids)
        if not expected:
            raise ValueError("状态管理快照至少需要一件装备 UID")
        deadline = time.monotonic() + max(0.0, float(timeout))
        with self._state_condition:
            while True:
                for scoped in self._scoped_equipment_snapshots:
                    if (
                        scoped.cursor > int(after_cursor)
                        and expected.issubset(scoped.event_uid_pairs)
                        and scoped.event_message is not None
                    ):
                        return dict(scoped.event_message)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待覆盖全部目标的状态管理快照超时")
                self._state_condition.wait(remaining)

    def _capture_scoped_equipment_snapshot(self, event: Mapping[str, Any]) -> None:
        """Keep guarded subset events for role verification without persisting them."""

        _generation, allowed_uids = self._full_inventory_guard()
        if not allowed_uids:
            return
        source_snapshot_id = self._full_inventory_guard_source_snapshot_id()
        payload = event.get("params") if event.get("method") == "event.inventory.snapshot" else event
        if not isinstance(payload, Mapping):
            return
        items = payload.get("items")
        if not isinstance(items, list):
            return
        parsed_items: list[dict[str, Any]] = []
        uid_pairs: set[tuple[int, int]] = set()
        event_uid_pairs: set[tuple[int, int]] = set()
        event_items: list[dict[str, Any]] = []
        valid_item_count = 0
        for item in items:
            if not isinstance(item, Mapping) or not isinstance(item.get("uid"), Mapping):
                return
            try:
                slot = int(item["uid"]["slot"])
                serial = int(item["uid"]["serial"])
            except (KeyError, TypeError, ValueError):
                return
            if slot <= 0 or serial <= 0:
                continue
            valid_item_count += 1
            event_uid_pairs.add((slot, serial))
            event_items.append(dict(item))
            # A scoped response may contain unrelated rows (for example a
            # just-acquired item).  It still supplies useful state for the
            # frozen inventory rows it does contain.  Unknown rows must not
            # enter either verification or the runtime overlay, but they also
            # must not discard the known rows from this same response.
            if (slot, serial) not in allowed_uids:
                continue
            normalized = dict(item)
            normalized["uid_slot"] = slot
            normalized["uid_serial"] = serial
            uid_pairs.add((slot, serial))
            parsed_items.append(normalized)
        with self._snapshot_guard_lock:
            if allowed_uids != self._snapshot_guard_uids:
                return
            self._snapshot_guard_packet_count += 1
            self._snapshot_guard_valid_item_count += valid_item_count
            self._snapshot_guard_known_item_count += len(uid_pairs)
            self._snapshot_guard_saw_full_uid_set |= uid_pairs == allowed_uids
            declared_count = payload.get("item_count")
            self._snapshot_guard_saw_declared_reduction |= (
                isinstance(declared_count, int)
                and declared_count >= 0
                and declared_count < len(allowed_uids)
            )
        if not uid_pairs:
            return
        with self._state_condition:
            self._scoped_snapshot_cursor += 1
            self._scoped_equipment_snapshots.append(
                ScopedEquipmentSnapshot(
                    cursor=self._scoped_snapshot_cursor,
                    items=tuple(parsed_items),
                    uid_pairs=frozenset(uid_pairs),
                    event_uid_pairs=frozenset(event_uid_pairs),
                    event_message={
                        "jsonrpc": str(event.get("jsonrpc") or "2.0"),
                        "method": "event.inventory.snapshot",
                        "params": {**dict(payload), "items": event_items},
                    },
                )
            )
            if source_snapshot_id is not None:
                self._pending_runtime_state_deltas.append((
                    source_snapshot_id,
                    tuple(parsed_items),
                    payload.get("observed_at_unix_ms")
                    if isinstance(payload.get("observed_at_unix_ms"), int)
                    else None,
                    payload.get("sequence")
                    if isinstance(payload.get("sequence"), int)
                    else None,
                ))
            # Several roles may emit one partial response each.  Bound the
            # cache while retaining enough recent responses for the batch.
            del self._scoped_equipment_snapshots[:-64]
            del self._pending_runtime_state_deltas[:-64]
            self._state_condition.notify_all()
        logger.info(
            "快照守卫事件诊断：声明件数={}，冻结件数={}，已知件数={}，完整标记={}，UID集合完整={}",
            payload.get("item_count"),
            len(allowed_uids),
            len(uid_pairs),
            payload.get("complete") is True,
            uid_pairs == allowed_uids,
        )
        if uid_pairs == allowed_uids:
            self._release_finished_guard_for_full_snapshot(allowed_uids)

    def _take_pending_runtime_state_deltas(
        self,
    ) -> list[tuple[int, tuple[dict[str, Any], ...], int | None, int | None]]:
        with self._state_condition:
            pending = self._pending_runtime_state_deltas
            self._pending_runtime_state_deltas = []
        return pending

    def _take_latest_event(self) -> dict[str, Any] | None:
        with self._event_lock:
            event = self._latest_inventory_event
            self._latest_inventory_event = None
            self._event_ready.clear()
        return event

    @staticmethod
    def _event_has_independent_character_instances(event: Mapping[str, Any]) -> bool:
        payload = event.get("params") if event.get("method") == "event.inventory.snapshot" else event
        return isinstance(payload, Mapping) and isinstance(payload.get("characters"), list)

    def _open_dao(self) -> UserDataDao:
        kwargs: dict[str, Any] = {}
        if not self.database_path.is_file():
            kwargs = {
                "account_id": self.account_id,
                "account_name": self.account_name,
            }
        return self._dao_factory(self.database_path, **kwargs)

    @staticmethod
    def _protocol_version(client: InventoryCoreClient) -> int | None:
        hello = client.hello_result
        if not isinstance(hello, Mapping):
            return None
        value = hello.get("protocol_version")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _run(self) -> None:
        run_inventory_sync(self)

    def _prune_raw_captures(self) -> None:
        prune_raw_captures(self)

    def stop(self, timeout: float = 10.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        thread = self._thread
        if thread is None:
            return
        self._stop_requested.set()
        self._event_ready.set()
        thread.join(timeout)
        if thread.is_alive():
            raise TimeoutError("背包同步服务未能在限定时间内停止")
        self._thread = None

    def wait_for_phase(self, phase: SyncPhase, timeout: float = 10.0) -> InventorySyncState:
        deadline = time.monotonic() + timeout
        with self._state_condition:
            while self._state.phase != phase:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"等待背包同步状态 {phase} 超时")
                self._state_condition.wait(remaining)
            return self._state

    def wait_for_snapshot(
        self,
        *,
        after_snapshot_id: int | None = None,
        timeout: float = 30.0,
    ) -> InventorySyncState:
        """等待首个稳定快照，或等待比装配前更新的稳定快照。"""

        deadline = time.monotonic() + timeout
        with self._state_condition:
            while True:
                snapshot_id = self._state.last_snapshot_id
                if snapshot_id is not None and (
                    after_snapshot_id is None or snapshot_id > after_snapshot_id
                ):
                    return self._state
                if self._state.phase == "error" and not self._state.running:
                    raise RuntimeError(self._state.error or self._state.message)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待新的稳定背包快照超时")
                self._state_condition.wait(remaining)

    def __enter__(self) -> "InventorySyncService":
        self.start()
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.stop()
