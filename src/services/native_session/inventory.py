# 将同修订读取去重与库存租约放在同一所有权模块，未知写入保留占用且不重发。
from __future__ import annotations
from time import monotonic
from src.integrations.nte_core_protocol import NteCoreProtocolError
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from copy import deepcopy
from contextlib import contextmanager
from threading import Event
from src.domain.all_item_snapshot import ALL_ITEMS_CAPABILITY
from src.services.inventory.sync.capture_wait import InventorySyncCancelled
from src.integrations.nte_core_protocol import NteCoreError, NteCoreRpcError, equipment_request_failure_kind
from src.observability import OperationContext, log_event


CHANGES_CAPABILITY = "snapshot.changes.v1"
CHANGE_SETTLE_SECONDS = 0.25


def require_current_sync_revision(snapshot, status):
    domain = snapshot["domain"]
    expected = tuple(snapshot[key] for key in ("providerId", "domainKey", "revision"))
    row = domain_status(status, domain)
    if (snapshot_change_key(status, domain) != expected or row.get("ready") is not True
            or row.get("dirty") is not False or row.get("enabled") is not True):
        raise NativeSnapshotPending("同步读取期间来源发生变化，等待重新采集。")


def domain_status(status, domain):
    if not isinstance(status, dict) or not isinstance(status.get("domains"), list):
        raise NteCoreProtocolError("原生变化状态格式无效。")
    rows = [row for row in status["domains"] if isinstance(row, dict) and row.get("domain") == domain]
    if len(rows) != 1:
        raise NteCoreProtocolError("原生变化状态缺少唯一的数据域。")
    return rows[0]


def snapshot_change_key(status, domain):
    row = domain_status(status, domain)
    revision = row.get("revision")
    provider = status.get("providerId")
    if (not isinstance(provider, str) or not provider
            or not isinstance(revision, str) or not revision.isascii() or not revision.isdecimal()
            or len(revision) > 20 or (len(revision) > 1 and revision.startswith("0"))
            or int(revision) > 18446744073709551615 or type(row.get("dirty")) is not bool
            or type(row.get("ready")) is not bool):
        raise NteCoreProtocolError("原生变化修订格式无效。")
    # 尚未就绪的数据域可无根身份，但新修订仍使旧观测失效。
    key = row.get("domainKey", "")
    if key is None and row["ready"] is False:
        key = ""
    if not isinstance(key, str) or len(key) > 1024 or (row["ready"] and not key):
        raise NteCoreProtocolError("原生变化会话身份无效。")
    return provider, key, revision


class NativeSnapshotChanges:
    def __init__(self, *, clock=monotonic):
        self._clock = clock
        self._seen = {}
        self._accepted = {}
        self._retry = {}

    def needs_refresh(self, status, domain):
        key, now = snapshot_change_key(status, domain), self._clock()
        retry = self._retry.get(domain)
        if retry is not None and retry[0] == key and now < retry[2]:
            return False
        if domain_status(status, domain)["ready"] is False:
            self._accepted.pop(domain, None)
        previous = self._seen.get(domain)
        if previous is None or previous[0] != key:
            self._seen[domain] = key, now
        accepted = self._accepted.get(domain)
        if accepted is None:
            return previous is None or now - self._seen[domain][1] >= CHANGE_SETTLE_SECONDS
        if key == accepted[0] and not domain_status(status, domain)["dirty"]:
            return False
        return now - self._seen[domain][1] >= CHANGE_SETTLE_SECONDS

    def accept(self, status, domain):
        key, now = snapshot_change_key(status, domain), self._clock()
        self._seen[domain] = key, now
        self._accepted[domain] = key, now
        self._retry.pop(domain, None)

    def defer(self, status, domain):
        """同一修订未完整时降低只读重试频率；新修订不继承旧等待。"""
        key, now = snapshot_change_key(status, domain), self._clock()
        previous = self._retry.get(domain)
        attempt = min(previous[1] + 1, 5) if previous and previous[0] == key else 1
        seconds = min(10, 2 ** (attempt - 1))
        self._retry[domain] = key, attempt, now + seconds
        return seconds

    def is_current(self, status, domain):
        accepted = self._accepted.get(domain)
        return (accepted is not None and snapshot_change_key(status, domain) == accepted[0]
                and domain_status(status, domain)["ready"] is True
                and not domain_status(status, domain)["dirty"])


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
        self._write_outcome_unknown = False
        self._write_operation = '游戏配装'
        self._changes = NativeSnapshotChanges()
        self._all_items_pending = None
        self._all_items_saved_revision = None
        self._all_items_retry_at = 0.0

    @property
    def maintenance_blocked(self):
        return self._equipment_context is not None or self._write_outcome_unknown

    @property
    def maintenance_description(self):
        if self._write_outcome_unknown:
            return f'{self._write_operation}的执行结果未确认，请先核对游戏中的实际状态；本次不更新插件，也不重发操作。'
        return f'{self._write_operation}正在执行或等待结果确认，请等待该功能结束，再更新插件。'

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
        # 先交付新背包给稳定器，再启动可能阻塞的其他域读取；角色域留下一轮继续。
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
    def equipment_batch(self, *, check_cancelled=None, timeout: float | None = None):
        """暂缓快照读取直到整批派发完成；通知仍由 DLL 累积。"""
        deadline = None if timeout is None else monotonic() + timeout
        def check():
            self._check()
            self._owner._guard("native_equipment")
            if check_cancelled is not None:
                check_cancelled()
            if deadline is not None and monotonic() >= deadline:
                raise TimeoutError("等待装配读取锁超时，本次未派发")

        with self._owner._snapshot_scope(check):
            if self._equipment_context is not None:
                yield self
                return
            # 调用方已冻结保存的完整库存；刷新进度不替代已知 UID 的派发门禁。
            status = self._owner.equipment_status(self._client,
                timeout=2.0 if deadline is None else min(2.0, max(0.001, deadline - monotonic())))
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

    def equipment_identity(self, *, timeout: float = 2.0):
        """读取当前指令来源身份，只复核而不改写已冻结批次。"""
        self._check()
        status = self._owner.equipment_status(self._client, timeout=timeout)
        if status.get("ready") is not True:
            raise NteCoreRpcError({"code": -32001, "message": "source_changed",
                                   "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"}})
        provider, domain = status.get("providerId"), status.get("domainKey")
        if provider is None or domain is None:
            raise NteCoreProtocolError("装备来源身份缺少正式提供方或对象域")
        if self._equipment_context is not None and (provider, domain) != self._equipment_context:
            raise NteCoreRpcError({"code": -32001, "message": "source_changed",
                                   "data": {"domain_code": "EQUIPMENT_REQUEST_REJECTED"}})
        self._check()
        return (provider, domain, self._client.executable_sha256, id(self._client))

    def _equipment_direct(self, method, **kwargs):
        self._check()
        self._owner._guard("native_equipment")
        if method not in {"set_item_locked", "set_item_discarded", "set_item_states"}:
            # 装卸期间仍可能切换目标角色对象域；逐条复核身份，不把 epoch 增长当切场。
            # 仓库状态批保留既有入批/拒绝后复核，不增加逐件状态探测。
            # 此时尚未派发，检查失败不进入下方“写结果未知”的异常分支。
            self.equipment_identity()
            self._owner._guard("native_equipment")
        self._write_operation = {
            'equip_one_key': '极速装配', 'set_item_locked': '仓库锁定／解锁',
            'set_item_discarded': '仓库弃置标记', 'set_item_states': '仓库锁定／弃置批量标记',
        }.get(method, '游戏装备调整')
        try:
            return getattr(self._client, method)(**kwargs)
        except NteCoreRpcError as error:
            # RPC 已返回不等于副作用已确认；未知结果跨批次保留维护占用，仍原样抛给调用方。
            if equipment_request_failure_kind(error) == "outcome_unknown":
                self._write_outcome_unknown = True
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
        except Exception:
            # 未被已知 RPC 拒绝分支处理的传输/协议异常保留未知写占用，不当成功也不重发。
            self._write_outcome_unknown = True
            raise

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
