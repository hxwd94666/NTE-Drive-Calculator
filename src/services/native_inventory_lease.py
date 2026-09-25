# 复用应用原生会话读取正式背包投影，不创建抓包进程或额外轮询线程。
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from threading import Event
from time import monotonic

from src.domain.all_item_snapshot import ALL_ITEMS_CAPABILITY
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.services.inventory_capture_wait import InventorySyncCancelled
from src.integrations.nte_core_protocol import NteCoreError, NteCoreRpcError, NteCoreProtocolError
from src.services.native_snapshot_changes import (
    CHANGES_CAPABILITY, NativeSnapshotChanges, domain_status, snapshot_change_key,
)
from src.observability import OperationContext, log_event


class NativeInventoryLease:
    native_capture = True

    def __init__(self, owner, client):
        self._owner, self._client = owner, client
        self._stopped = Event()
        self._released = False
        self._started = False
        self._handlers = {}
        self._sequence = 0
        self._snapshot_ready = False
        self._equipment_context = None
        self._changes = NativeSnapshotChanges()
        self._all_items_pending = None
        self._all_items_saved_revision = None
        self._all_items_retry_at = 0.0

    @property
    def snapshot_ready(self):
        return self._snapshot_ready and not self._released and not self._stopped.is_set()

    @property
    def hello_result(self):
        return self._client.hello_result

    @property
    def executable_sha256(self):
        return self._client.executable_sha256

    def _check(self):
        if self._released or self._stopped.is_set() or self._owner._inventory_lease is not self:
            raise InventorySyncCancelled("原生背包同步已停止。")
        self._owner._check_projection(self._client)

    def start(self):
        self._check()
        return self

    def add_event_handler(self, method, handler):
        self._check()
        self._handlers.setdefault(method, []).append(handler)

    def remove_event_handler(self, method, handler):
        handlers = self._handlers.get(method, [])
        if handler in handlers:
            handlers.remove(handler)

    def start_capture(self, *, profile, raw_capture="disabled", **kwargs):
        self._check()
        if profile != "inventory" or raw_capture != "disabled" or kwargs.get("wait_for_game"):
            raise ValueError("原生背包同步仅接受正式库存读取。")
        self._started = True
        return {"capture_status": "running", "native_snapshot_ready": False}

    def status(self):
        with self._owner._snapshot_scope(self._check):
            return self._status()

    def confirm_inventory_snapshot(self, metadata):
        """保存前只复核候选身份，不采集新快照或刷新角色。"""
        with self._owner._snapshot_scope(self._check):
            try:
                current = self._change_status()
            except NteCoreRpcError as error:
                if not self._pending_error(error):
                    raise
                self._snapshot_ready = False
                return False
            if current is None:
                return self.snapshot_ready
            if not isinstance(metadata, dict):
                raise NteCoreProtocolError("待保存的原生背包缺少修订身份。")
            expected = tuple(metadata.get(key) for key in ("providerId", "domainKey", "revision"))
            self._snapshot_ready = (self._changes.is_current(current, "inventory")
                                    and snapshot_change_key(current, "inventory") == expected)
            self._check()
            return self.snapshot_ready

    def _status(self):
        self._check()
        self._snapshot_ready = False
        if not self._started:
            return {"capture_status": "idle", "native_snapshot_ready": False}
        try:
            status = self._change_status()
            snapshot = self._refresh_domain("inventory", status)
        except NativeSnapshotPending as error:
            return {"capture_status": "running", "native_snapshot_ready": False, "message": str(error)}
        except NteCoreRpcError as error:
            if (error.domain_code == "NATIVE_SNAPSHOT_INCOMPLETE"
                    or error.message in {"not_ready", "source_changed", "snapshot_not_found", "disabled"}
                    or (error.code == -32001 and error.message == "control_timeout")):
                return {"capture_status": "running", "native_snapshot_ready": False,
                        "message": ("本次背包读取超时，等待游戏就绪后自动重试；已保存背包保持不变。"
                                    if error.message == "control_timeout" else
                                    "读取期间背包有变化，正在自动重读；已保存背包保持不变。"
                                    if error.message == "source_changed" else
                                    "正在等待游戏提供本次完整背包；已保存背包保持不变。")}
            raise
        self._check()
        if snapshot is not None:
            self._emit_inventory(snapshot)
        character = None
        character_error = None
        # Return a newly read inventory to the stabilizer before starting another
        # potentially slow game-thread read. Tracked domains resume next poll.
        defer_character = snapshot is not None and status is not None
        if not defer_character and "native_character_profile_v1" in (self.hello_result or {}).get("capabilities", ()):
            try:
                character = self._refresh_domain("character", self._change_status())
            except (NativeSnapshotPending, NteCoreRpcError) as error:
                if isinstance(error, NteCoreRpcError) and not self._pending_error(error):
                    raise
                character_error = "角色状态尚未就绪，保留已保存养成。"
        self._check()
        current = self._change_status()
        self._snapshot_ready = current is None or self._changes.is_current(current, "inventory")
        if character is not None and current is not None and not self._changes.is_current(current, "character"):
            character = None
        all_items = None
        all_items_error = None
        if (self.snapshot_ready and snapshot is None and character is None and character_error is None
                and current is not None and self._equipment_context is None
                and ALL_ITEMS_CAPABILITY in (self.hello_result or {}).get("capabilities", ())):
            try:
                all_items = self._all_items_for_storage(current)
            except (NativeSnapshotPending, NteCoreError) as error:
                self._all_items_retry_at = monotonic() + 30.0
                all_items_error = type(error).__name__
            self._check()
            current = self._change_status()
            self._snapshot_ready = self._changes.is_current(current, "inventory")
        return {"capture_status": "running", "native_snapshot_ready": self.snapshot_ready,
                "native_change_pending": (not self.snapshot_ready and current is not None
                                          and bool(domain_status(current, "inventory").get("domainKey"))),
                "message": "背包已同步，正在后台监听变化。" if self.snapshot_ready else "正在等待装备变化稳定。",
                **({"native_character_snapshot": character} if character is not None else {}),
                **({"native_all_item_snapshot": all_items} if all_items is not None else {}),
                **({"native_all_item_error": all_items_error} if all_items_error else {}),
                **({"native_character_error": character_error} if character_error else {})}

    def _all_items_for_storage(self, status):
        revision = snapshot_change_key(status, "inventory")
        if self._all_items_pending is not None:
            pending_revision = tuple(self._all_items_pending[key] for key in ("providerId", "domainKey", "revision"))
            if pending_revision == revision:
                return self._all_items_pending
            self._all_items_pending = None
        if revision == self._all_items_saved_revision or monotonic() < self._all_items_retry_at:
            return None
        self._all_items_pending = self._owner.read_all_items(self._client, self._check)
        return self._all_items_pending

    def confirm_all_item_snapshot_saved(self, snapshot):
        self._check()
        if self._all_items_pending is snapshot:
            self._all_items_saved_revision = tuple(snapshot[key] for key in ("providerId", "domainKey", "revision"))
            self._all_items_pending = None

    @staticmethod
    def _pending_error(error):
        return (error.code == -32001 and error.message == "control_timeout") or error.domain_code in {"NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_MAPPING_UNSUPPORTED"} or error.message in {
            "not_ready", "source_changed", "snapshot_not_found", "disabled",
        }

    def _change_status(self):
        self._check()
        if CHANGES_CAPABILITY not in (self.hello_result or {}).get("capabilities", ()):
            return None
        result = self._client.call("native.snapshot.status", {}, check_cancelled=self._check)
        self._check()
        return result

    def _refresh_domain(self, domain, status):
        if status is not None and not self._changes.needs_refresh(status, domain):
            return None
        try:
            snapshot = self._owner._read_projection(self._client, domain, self._check)
        except (NativeSnapshotPending, NteCoreRpcError) as error:
            if status is not None and (isinstance(error, NativeSnapshotPending) or self._pending_error(error)):
                delay = self._changes.defer(status, domain)
                log_event("INFO", "native_sync.retry_deferred", "同一修订读取未完成，降低重试频率",
                          OperationContext.create("native_sync"), domain=domain, retry_after_seconds=delay,
                          error_type=type(error).__name__)
            raise
        if status is not None:
            latest = self._change_status()
            expected = snapshot.get("providerId"), snapshot.get("domainKey"), snapshot.get("revision")
            if (snapshot_change_key(latest, domain) != expected
                    or domain_status(latest, domain).get("ready") is not True
                    or domain_status(latest, domain)["dirty"] or snapshot.get("dirty") is not False):
                raise NativeSnapshotPending("读取期间游戏状态发生变化，正在重新同步。")
            self._changes.accept(latest, domain)
        return snapshot

    def _emit_inventory(self, snapshot):
        timestamp = snapshot["observedUnixUs"]
        generation = snapshot["generation"]
        if not timestamp.isdecimal() or not generation.isdecimal():
            raise NteCoreProtocolError("原生同步时间或快照代次格式无效。")
        self._sequence += 1
        metadata = {key: value for key, value in snapshot.items() if key not in {"items", "characters"}}
        payload = {"generation": int(generation), "sequence": self._sequence,
                   "observed_at_unix_ms": int(timestamp) // 1000, "complete": True,
                   "item_count": len(snapshot["items"]), "items": snapshot["items"],
                   "character_count": len(snapshot["characters"]), "characters": snapshot["characters"],
                   "native_snapshot": metadata}
        event = {"jsonrpc": "2.0", "method": "event.inventory.snapshot", "params": payload}
        for handler in tuple(self._handlers.get("event.inventory.snapshot", ())):
            self._check()
            handler(deepcopy(event))
        self._check()

    def request_stop(self):
        self._stopped.set()
        self._snapshot_ready = False

    def stop_capture(self):
        self.request_stop()
        self._started = False
        return {"capture_status": "stopped", "native_snapshot_ready": False}

    def close(self):
        if not self._released:
            self.stop_capture()
            self._released = True
            self._handlers.clear()
            self._owner._release_inventory(self)

    @contextmanager
    def equipment_batch(self):
        """暂缓快照读取直到整批派发完成；通知仍由 DLL 累积。"""
        def check():
            self._check()
            self._owner._guard("native_equipment")

        with self._owner._snapshot_scope(check):
            if self._equipment_context is not None:
                yield self
                return
            # The application pins a saved complete inventory before dispatch.
            # Refresh readiness must not block commands against those known UIDs.
            status = self._owner.equipment_status(self._client)
            if status.get("ready") is not True:
                raise NteCoreRpcError({"code": -32001, "message": "请等待装备接口就绪后重试。",
                                       "data": {"domain_code": "NATIVE_SNAPSHOT_INCOMPLETE"}})
            self._equipment_context = tuple(status.get(key) for key in ("providerId", "domainKey"))
            try:
                check()
                yield self
            finally:
                self._equipment_context = None
                self._snapshot_ready = False

    def _equipment(self, method, **kwargs):
        if self._equipment_context is not None:
            return self._equipment_direct(method, **kwargs)
        with self.equipment_batch():
            return self._equipment_direct(method, **kwargs)

    def _equipment_direct(self, method, **kwargs):
        self._check()
        self._owner._guard("native_equipment")
        try:
            return getattr(self._client, method)(**kwargs)
        except NteCoreRpcError as error:
            if error.code != -32001 or error.message != "source_changed" or error.domain_code is not None:
                raise
            self._check()
            current = self._owner.equipment_status(self._client)
            current_context = tuple(current.get(key) for key in ("providerId", "domainKey"))
            self._check()
            if current.get("ready") is True and current_context == self._equipment_context:
                raise
            raise NteCoreRpcError({
                "code": -32001,
                "message": "source_changed",
                "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"},
            }) from error

    def equip_one_key(self, **kwargs):
        return self._equipment("equip_one_key", **kwargs)

    def equip_module(self, **kwargs):
        return self._equipment("equip_module", **kwargs)

    def equip_core(self, **kwargs):
        return self._equipment("equip_core", **kwargs)

    def unequip_module(self, **kwargs):
        return self._equipment("unequip_module", **kwargs)

    def unequip_core(self, **kwargs):
        return self._equipment("unequip_core", **kwargs)

    def unequip_all(self, **kwargs):
        return self._equipment("unequip_all", **kwargs)

    def move_module_to_character(self, **kwargs):
        return self._equipment("move_module_to_character", **kwargs)

    def move_core_to_character(self, **kwargs):
        return self._equipment("move_core_to_character", **kwargs)

    def set_item_discarded(self, **kwargs):
        return self._equipment("set_item_discarded", **kwargs)

    def set_item_states(self, **kwargs):
        return self._equipment("set_item_states", **kwargs)

    def set_item_locked(self, **kwargs):
        return self._equipment("set_item_locked", **kwargs)
