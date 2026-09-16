# 由单一应用所有者持有原生 Core，租约隔离战报并保护同步与退出边界。
from __future__ import annotations

from threading import Event, RLock
from contextlib import contextmanager
from concurrent.futures import CancelledError
from time import monotonic
from typing import Any, Callable

from src.integrations.nte_core import NteCoreClient
from src.integrations.nte_core_protocol import NteCoreError, NteCoreRpcError, NteCoreTimeoutError, NteCoreProtocolError
from src.integrations.native_inventory_snapshot import NativeSnapshotPending, read_native_projection
from src.integrations.native_snapshot_baseline import NativeSnapshotBaseline
from src.services.inventory_capture_wait import InventorySyncCancelled
from src.observability import OperationContext, log_event


SNAPSHOT_DOMAINS = ("character", "inventory", "team", "environment")
NATIVE_REFRESH_TIMEOUT_SECONDS = 65.0
NATIVE_BATTLE_HANDOFF_TIMEOUT_SECONDS = 10.0
NATIVE_EQUIPMENT_CAPABILITIES = frozenset({"equipment", "native_equipment_v1"})


class NativeGameSession:
    def __init__(
        self, factory: Callable[[], NteCoreClient], guard: Callable[[str], None],
        context_key: Callable[[], object] | None = None,
        diagnostics_enabled: Callable[[], bool] | None = None,
    ) -> None:
        self._factory, self._guard = factory, guard
        self._context_key = context_key
        self._diagnostics_enabled = diagnostics_enabled
        self._diagnostics_applied = None
        self._connected_context: object = None
        self._client: NteCoreClient | None = None
        self._lock = RLock()
        self._snapshot_lock = RLock()
        self._inventory_lease = None
        self._lease: NativeBattleLease | None = None
        self._closing = False
        self._close_requested = Event()
        self._permanently_closed = Event()
        self._failed = False
        self._snapshots_disabled = False
        self._refresh_active = None
        self._battle_requested = Event()
        self._baseline = NativeSnapshotBaseline()

    @property
    def battle_active(self) -> bool:
        with self._lock:
            return self._lease is not None

    def _current_context(self) -> object:
        return self._context_key() if self._context_key is not None else None

    def _context_matches(self) -> bool:
        return self._connected_context == self._current_context()

    def _connect(self) -> NteCoreClient:
        if self._closing or self._close_requested.is_set():
            raise RuntimeError("原生会话正在收尾，等待当前战报结束。")
        if self._client is None and self._lease is not None:
            raise RuntimeError("失效的原生战报租约尚未释放，不能重连。")
        if self._client is None and self._refresh_active is not None:
            raise RuntimeError("原生刷新正在收尾，暂时不能重连。")
        if self._client is not None and (
            self._failed or not self._context_matches() or not self._client.is_running
        ):
            if self._lease is not None:
                raise RuntimeError("当前原生战报尚未释放失效会话，不能重连。")
            self._finish_close()
        if self._client is None:
            context = self._current_context()
            client = self._factory()
            try:
                if self._close_requested.is_set():
                    raise RuntimeError("原生连接已取消。")
                client.start()
                if self._close_requested.is_set():
                    raise RuntimeError("原生连接已取消。")
                if not client.native_capture:
                    raise RuntimeError("原生会话来源不符，未连接。")
                if context != self._current_context():
                    raise RuntimeError("原生连接期间账号上下文已改变。")
            except Exception:
                client.close()
                raise
            self._client = client
            self._baseline = NativeSnapshotBaseline()
            client.add_event_handler("event.native.diagnostics.error", self._archive_failed)
            self._connected_context = context
            self._failed = False
            self._snapshots_disabled = False
        if self._diagnostics_enabled is not None:
            enabled = bool(self._diagnostics_enabled())
            applied = (self._client, enabled)
            if applied != self._diagnostics_applied:
                capabilities = (self._client.hello_result or {}).get("capabilities", ())
                if "native_snapshot_archive_v1" in capabilities:
                    try:
                        self._client.call("native.diagnostics.configure", {"enabled": enabled})
                    except NteCoreRpcError as error:
                        if error.domain_code != "NATIVE_SNAPSHOT_ARCHIVE_WRITE_FAILED":
                            raise
                        self._archive_failed()
                elif enabled:
                    raise RuntimeError("当前 Core 不支持 DLL 原始快照排错保存，请更新随附组件。")
                self._diagnostics_applied = applied
        return self._client

    @staticmethod
    def _archive_failed(_event=None):
        from src.utils.logger import logger
        logger.warning("DLL 原始快照未能保存，已停止本次诊断归档；请检查账号日志目录可写性与磁盘空间。")

    def inspect(self, *, refresh: bool = False, check_equipment: bool = True) -> dict[str, Any]:
        with self._snapshot_scope(self._check_inspection):
            return self._inspect_snapshot(refresh=refresh, check_equipment=check_equipment)

    def _check_inspection(self, client=None):
        self._guard("native_sync")
        self._check_snapshot_priority()
        if self._close_requested.is_set() or (client is not None and (
            self._client is not client or not self._context_matches()
        )):
            if client is None:
                raise RuntimeError("原生会话正在收尾。")
            raise InventorySyncCancelled("原生检测已停止或账号上下文已改变。")

    def _check_snapshot_priority(self):
        if self._battle_requested.is_set():
            raise InventorySyncCancelled("本次原生刷新已让出会话，正在开始战报。")

    @contextmanager
    def _snapshot_scope(self, check):
        while True:
            check()
            if self._snapshot_lock.acquire(timeout=0.1):
                break
        try:
            check()
            yield
        finally:
            self._snapshot_lock.release()

    def _refresh_snapshot(self, client, params, check):
        token = object()
        with self._lock:
            check()
            if self._lease is not None:
                raise NativeSnapshotPending("正在采集原生战报，结束后再刷新背包或角色状态。")
            self._refresh_active = token
        try:
            result = client.call("native.snapshot.refresh", params, timeout=NATIVE_REFRESH_TIMEOUT_SECONDS,
                                 check_cancelled=check)
            check()
            return result
        except (InventorySyncCancelled, CancelledError, PermissionError, NteCoreTimeoutError):
            # Battle acquisition is excluded while this refresh owns the client.
            with self._lock:
                abort = self._refresh_active is token and self._client is client and self._lease is None
                if abort:
                    self._client = None
                    self._failed = True
            if abort:
                client.abort()
            raise
        finally:
            with self._lock:
                if self._refresh_active is token:
                    self._refresh_active = None

    def _inspect_snapshot(self, *, refresh: bool = False, check_equipment: bool = True) -> dict[str, Any]:
        with self._lock:
            self._check_inspection()
            client = self._connect()
        try:
            capabilities = (client.hello_result or {}).get("capabilities", [])
            # Status calls must not hold the lock needed by a battle stop timeout abort.
            status = client.status()
            domains = {}
            domain_errors: dict[str, dict[str, Any]] = {}
            if any(f"{domain}.snapshot.v1" in capabilities for domain in SNAPSHOT_DOMAINS):
                if refresh:
                    for domain in SNAPSHOT_DOMAINS:
                        if f"{domain}.snapshot.v1" in capabilities:
                            with self._lock:
                                if self._lease is not None or self._inventory_lease is not None:
                                    break
                                self._guard("native_sync")
                                if self._close_requested.is_set():
                                    raise RuntimeError("原生会话正在收尾。")
                                if self._client is not client or not self._context_matches():
                                    raise RuntimeError("原生刷新期间账号上下文已改变。")
                            try:
                                check = lambda: self._check_inspection(client)
                                if "snapshot.changes.v1" not in capabilities:
                                    self._refresh_snapshot(client, {"domain": domain}, check)
                                elif ((domain == "character" and "native_character_profile_v1" in capabilities)
                                      or (domain == "inventory" and "native_inventory_dto_v1" in capabilities)):
                                    self._read_projection(client, domain, check)
                                else:
                                    from src.integrations.native_battle_snapshot import read_native_raw_domain, validate_native_snapshot_current
                                    baseline = self._baseline
                                    current = client.call("native.snapshot.status", {}, check_cancelled=check)
                                    if baseline.get(domain, current) is None:
                                        def call(method, params):
                                            if method == "native.snapshot.refresh":
                                                return self._refresh_snapshot(client, params, check)
                                            return client.call(method, params, check_cancelled=check)
                                        raw = read_native_raw_domain(call, check, domain)
                                        validate_native_snapshot_current({"domains": {domain: raw}}, call("native.snapshot.status", {}))
                                        check()
                                        baseline.put(domain, raw)
                            except NativeSnapshotPending:
                                break
                            except NteCoreRpcError as error:
                                # These exact provider rejections invalidate only this domain.
                                if error.code != -32001 or error.message not in {"not_ready", "source_changed"}:
                                    raise
                                reason = error.data.get("reason")
                                domain_errors[domain] = {
                                    "code": error.code,
                                    "message": error.message,
                                    "reason": reason if isinstance(reason, str) else "",
                                }
                self._guard("native_sync")
                domains = client.call("native.snapshot.status", {})
            equipment = None
            # 装备接口检测在游戏线程执行，后台同步观察不应反复发起；实际装配仍逐次复核。
            if check_equipment and NATIVE_EQUIPMENT_CAPABILITIES.issubset(capabilities):
                try:
                    equipment = self.equipment_status(client)
                except NteCoreRpcError as error:
                    if error.code != -32001 or error.message not in {"not_ready", "source_changed"}:
                        raise
                    equipment = {"ready": False, "reason": error.message}
            with self._lock:
                self._guard("native_sync")
                if self._client is not client or not self._context_matches():
                    raise RuntimeError("原生检测结果已过期。")
                return {
                    "hello": client.hello_result or {}, "status": status,
                    "domains": domains, "domain_errors": domain_errors,
                    "equipment": equipment,
                    "inventory_snapshot_ready": bool(self._inventory_lease is not None and self._inventory_lease.snapshot_ready),
                }
        except Exception:
            with self._lock:
                if self._client is client:
                    self._failed = True
                    if self._lease is None:
                        self._finish_close()
            raise

    def inventory_client(self):
        from src.services.native_inventory_lease import NativeInventoryLease
        with self._lock:
            self._guard("native_sync")
            self._check_snapshot_priority()
            if self._inventory_lease is not None:
                raise RuntimeError("已有背包同步使用原生会话。")
            client = self._connect()
            self._require_projection_capability(client, "inventory")
            self._inventory_lease = NativeInventoryLease(self, client)
            return self._inventory_lease

    @staticmethod
    def _require_projection_capability(client, domain):
        cap = "native_inventory_dto_v1" if domain == "inventory" else "native_character_profile_v1"
        capabilities = (client.hello_result or {}).get("capabilities", [])
        if cap not in capabilities or f"{domain}.snapshot.v1" not in capabilities:
            raise NteCoreRpcError({"code": -32001, "message": "当前原生组件不支持此项正式同步，请检查组件版本。",
                                   "data": {"domain_code": "NATIVE_CAPABILITY_MISSING"}})

    def _check_projection(self, client, check=None):
        with self._lock:
            self._check_snapshot_priority()
            try:
                self._guard("native_sync")
            except PermissionError as error:
                raise InventorySyncCancelled("原生同步权限已撤销。") from error
            if (self._close_requested.is_set() or self._client is not client
                    or not self._context_matches() or self._failed):
                raise InventorySyncCancelled("原生同步已取消或账号上下文已改变。")
        if check is not None:
            check()

    def _read_projection(self, client, domain, check=None):
        current = lambda: self._check_projection(client, check)
        with self._snapshot_scope(current):
            self._check_projection(client, check)
            self._require_projection_capability(client, domain)
            baseline = self._baseline
            timings, counts = {}, {}
            completed = False
            started = monotonic()
            refreshed_header = None
            try:
                def call(method, params):
                    nonlocal refreshed_header
                    stage = {"native.snapshot.refresh": "refresh", "native.snapshot.status": "status",
                             "native.snapshot.page": "raw_pages"}.get(method, "projection_pages")
                    before = monotonic()
                    try:
                        if method == "native.snapshot.refresh":
                            refreshed_header = self._refresh_snapshot(client, params, current)
                            return refreshed_header
                        return client.call(method, params, check_cancelled=current)
                    finally:
                        timings[stage] = timings.get(stage, 0.0) + (monotonic() - before) * 1000
                        counts[stage] = counts.get(stage, 0) + 1
                tracked = "snapshot.changes.v1" in (client.hello_result or {}).get("capabilities", ())
                if tracked:
                    cached = baseline.get(domain, call("native.snapshot.status", {}))
                    if cached is not None and cached["projection"] is not None:
                        return cached["projection"]
                from src.integrations.native_inline_snapshot import INLINE_RAW_CAPABILITY, InlineSnapshotPages
                inline = (InlineSnapshotPages(call, domain) if tracked and INLINE_RAW_CAPABILITY in
                          (client.hello_result or {}).get("capabilities", ()) else None)
                projection = read_native_projection(inline.call if inline else call, current, domain=domain)
                if tracked:
                    from src.integrations.native_battle_snapshot import read_native_raw_domain, validate_native_snapshot_current
                    validate_native_snapshot_current({"domains": {domain: projection}}, call("native.snapshot.status", {}))
                    raw = (inline.read(projection, current) if inline else
                           read_native_raw_domain(call, current, domain, header=refreshed_header))
                    validate_native_snapshot_current({"domains": {domain: raw}}, call("native.snapshot.status", {}))
                    baseline.put(domain, raw, projection)
                completed = True
                return projection
            except NteCoreError as error:
                if isinstance(error, NteCoreRpcError) and (
                    error.domain_code in {"NATIVE_MAPPING_UNSUPPORTED", "NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_CAPABILITY_MISSING"}
                    or error.message in {"not_ready", "source_changed", "snapshot_not_found", "disabled"}
                    or (error.code == -32001 and error.message == "control_timeout")
                ):
                    raise
                with self._lock:
                    if self._client is client:
                        self._failed = True
                        if self._lease is None:
                            self._finish_close()
                raise

            finally:
                if "refresh" in counts:
                    log_event("INFO", "native_sync.projection_read", "原生同步读取分段耗时",
                              OperationContext.create("native_sync"), domain=domain, completed=completed,
                              duration_ms=round((monotonic() - started) * 1000, 2),
                              stage_duration_ms={key: round(value, 2) for key, value in timings.items()},
                              stage_call_count=counts)

    def read_character_profiles(self, *, check=None):
        with self._lock:
            self._guard("native_sync")
            self._check_snapshot_priority()
            if check is not None:
                check()
            client = self._connect()
        return self._read_projection(client, "character", check)

    def _release_inventory(self, lease):
        with self._lock:
            if self._inventory_lease is lease:
                self._inventory_lease = None

    def battle_client(self, *, check: Callable[[], None] | None = None) -> NativeBattleLease:
        with self._lock:
            if check is not None:
                check()
            self._guard("native_battle")
            if self._lease is not None or self._battle_requested.is_set():
                raise RuntimeError("已有原生战报占用此会话。")
            context = self._current_context()
            self._battle_requested.set()
        acquired = False
        deadline = monotonic() + NATIVE_BATTLE_HANDOFF_TIMEOUT_SECONDS
        try:
            while True:
                if check is not None:
                    check()
                self._guard("native_battle")
                if self._close_requested.is_set() or context != self._current_context():
                    raise RuntimeError("战报启动已取消或账号上下文已改变。")
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise RuntimeError("原生刷新未能及时收尾，未开始战报，请稍后重试。")
                if self._snapshot_lock.acquire(timeout=min(0.1, remaining)):
                    acquired = True
                    break
            # The former snapshot owner has completed cancellation and Core teardown.
            with self._lock:
                if check is not None:
                    check()
                self._guard("native_battle")
                if self._close_requested.is_set() or context != self._current_context():
                    raise RuntimeError("战报启动已取消或账号上下文已改变。")
                client = self._connect()
                self._guard("native_battle")
                self._lease = NativeBattleLease(self, client, check=check)
                return self._lease
        finally:
            self._battle_requested.clear()
            if acquired:
                self._snapshot_lock.release()

    def equipment_status(self, client):
        self._check_projection(client)
        if not NATIVE_EQUIPMENT_CAPABILITIES.issubset((client.hello_result or {}).get("capabilities", ())):
            raise NteCoreRpcError({"code": -32001, "message": "当前原生组件不支持装备执行。",
                                   "data": {"domain_code": "NATIVE_CAPABILITY_MISSING"}})
        status = client.call("equipment.status", {}, check_cancelled=lambda: self._check_projection(client))
        self._check_projection(client)
        if not isinstance(status, dict):
            raise NteCoreError("原生装备状态格式无效。")
        return status

    def _disable_snapshots(self) -> None:
        if self._client is None or self._snapshots_disabled:
            return
        self._snapshots_disabled = True
        capabilities = (self._client.hello_result or {}).get("capabilities", [])
        for domain in SNAPSHOT_DOMAINS:
            if f"{domain}.snapshot.v1" in capabilities:
                try:
                    self._client.call("native.snapshot.disable", {"domain": domain}, timeout=1.5)
                except Exception:
                    self._failed = True

    def close(self) -> None:
        self.request_close()
        with self._lock:
            self._closing = True
            self._disable_snapshots()
            if self._lease is None:
                self._finish_close()

    def request_close(self, *, permanent: bool = False) -> None:
        """Nonblocking revocation; the observer owner performs RPC teardown later."""
        if permanent:
            self._permanently_closed.set()
        self._close_requested.set()

    def _finish_close(self) -> None:
        client, self._client = self._client, None
        self._closing = False
        self._failed = False
        self._connected_context = None
        self._baseline = NativeSnapshotBaseline()
        if not self._permanently_closed.is_set():
            self._close_requested.clear()
        if client is not None:
            client.close()

    def _release_battle(self, lease: NativeBattleLease) -> None:
        with self._lock:
            if self._lease is not lease:
                return
            self._lease = None
            if self._closing or self._failed:
                self._finish_close()

    def _abort_battle(self, lease: NativeBattleLease) -> None:
        with self._lock:
            if self._lease is not lease:
                return
            self._failed = True
            client, self._client = self._client, None
        # Abort does not wait on the owner lock held by a blocked stop RPC.
        if client is not None:
            client.abort()


class NativeBattleLease:
    """An existing battle can drain final reads after mode revocation."""
    native_capture = True

    def __init__(self, owner: NativeGameSession, client: NteCoreClient, *, check=None) -> None:
        self._owner, self._client = owner, client
        self._external_check = check
        self._released = False
        self._aborted = False
        self._handlers: dict[tuple[Any, Callable], Callable] = {}
        self._scope_snapshots = {}
        from src.services.native_battle_preparation import NativeBattlePreparation
        self._preparation = NativeBattlePreparation()

    @property
    def hello_result(self):
        return self._client.hello_result

    @property
    def executable_sha256(self):
        return self._client.executable_sha256

    def _check(self, *, new_work: bool = False) -> None:
        with self._owner._lock:
            if self._released or self._aborted or self._owner._lease is not self:
                raise RuntimeError("原生战报租约已失效。")
            if not self._owner._context_matches():
                raise RuntimeError("原生战报账号上下文已改变。")
            if new_work:
                if self._external_check is not None:
                    self._external_check()
                if self._owner._closing or self._owner._close_requested.is_set():
                    raise RuntimeError("原生会话正在收尾。")
                self._owner._guard("native_battle")

    def start(self):
        self._check(new_work=True)
        return self

    def add_event_handler(self, event, handler):
        self._check(new_work=True)
        key = (event, handler)
        if key in self._handlers:
            return

        def guarded(payload):
            try:
                self._check()
                self._owner._guard("native_battle")
            except (PermissionError, RuntimeError):
                return
            handler(payload)

        self._handlers[key] = guarded
        self._client.add_event_handler(event, guarded)

    def remove_event_handler(self, event, handler):
        wrapped = self._handlers.pop((event, handler), None)
        if wrapped is not None:
            self._client.remove_event_handler(event, wrapped)

    def start_capture(self, **kwargs):
        self._check(new_work=True)
        if kwargs.get("profile") != "combat":
            raise ValueError("原生战报租约只接受 combat 采集。")
        if "native_battle_scope_snapshot_v1" not in (self.hello_result or {}).get("capabilities", ()):
            raise RuntimeError("采集组件缺少第一击快照能力，请更新采集组件。")
        self._scope_snapshots.clear()
        from src.services.native_battle_preparation import NativeBattlePreparation
        self._preparation = NativeBattlePreparation()
        result = self._client.start_capture(**kwargs)
        # The UI startup token must not cancel accepted hits during normal stop.
        # Snapshot reads retain their own stop check; account and mode checks remain active.
        self._external_check = None
        return result

    def observe_battle_scopes(self, record, *, final=False, stop_requested=None):
        from src.services.native_battle_scopes import scope_attempts
        from copy import deepcopy
        attempts = scope_attempts(record)
        candidates = {scope: self._preparation.take_matching(attempt, record)
                      for scope, attempt in attempts.items() if scope not in self._scope_snapshots}
        prepared = None
        if not final:
            prepared = self._preparation.prepare(record, attempts, lambda: self._read_battle_snapshot(stop_requested))
        changed = False
        for scope in tuple(self._scope_snapshots):
            if scope not in attempts or self._scope_snapshots[scope]["attempt_id"] != attempts[scope]["attemptId"]:
                del self._scope_snapshots[scope]
                changed = True
        for scope, attempt in attempts.items():
            if scope in self._scope_snapshots:
                continue
            frozen = candidates.get(scope) or self._preparation.take_matching(attempt, record)
            if frozen is not None:
                frozen["binding"] = "first_hit_revision"
            elif final:
                frozen = {"state": "unavailable", "domains": {}, "missing": ["first_hit_not_observed_live"]}
            else:
                if prepared is not None:
                    frozen = deepcopy(prepared)
                elif self._preparation.key is not None:
                    if self._preparation.snapshot is None:
                        continue  # A pending baseline retains the first hit's attempt, not a failed snapshot.
                    frozen = deepcopy(self._preparation.snapshot)
                else:
                    frozen = self._read_battle_snapshot(stop_requested)
                if frozen.get("state") == "source_changed" or (frozen.get("state") == "observed" and
                        (len(frozen.get("domains") or {}) != 4 or
                         "inventory_projection" not in frozen or "character_projection" not in frozen)):
                    continue
                frozen["binding"] = "first_hit_revision"
                # Preserve the immutable observation. First-hit/continuity validation
                # uses the final retained context evidence in select_scope_builds;
                # this record predates the blocking read and can still be incomplete.
            self._scope_snapshots[scope] = {"attempt_id": attempt["attemptId"], "snapshot": frozen}
            changed = True
        return {"state": "scoped", "scopes": dict(self._scope_snapshots)} if changed else None

    def _read_battle_snapshot(self, stop_requested):
        from src.integrations.native_battle_snapshot import freeze_native_battle_snapshot
        def check():
            self._check(new_work=True)
            if stop_requested is not None and stop_requested():
                raise CancelledError("战报停止，取消未完成的配置读取。")
        try:
            with self._owner._snapshot_scope(check):
                frozen = freeze_native_battle_snapshot(self._client, check, baseline=self._owner._baseline)
        except CancelledError:
            if stop_requested is None or not stop_requested():
                raise
            frozen = {"state": "unavailable", "domains": {}, "missing": ["snapshot_read_cancelled_by_stop"]}
        except (NteCoreRpcError, NteCoreProtocolError, NteCoreTimeoutError) as error:
            # Configuration is optional evidence. A rejected or malformed
            # snapshot must not discard the independently collected hits.
            self._check(new_work=True)
            diagnostic = {"error_type": type(error).__name__}
            if isinstance(error, NteCoreRpcError):
                diagnostic["rpc_code"] = error.code
                # Only known contract codes may enter logs; provider messages can contain private data.
                known_codes = {"NATIVE_SNAPSHOT_NOT_FOUND", "NATIVE_SNAPSHOT_UNAVAILABLE",
                               "NATIVE_SNAPSHOT_INCOMPLETE", "NATIVE_MAPPING_UNSUPPORTED",
                               "REQUEST_IN_PROGRESS", "INVALID_PARAMS"}
                diagnostic["domain_code"] = (error.domain_code if error.domain_code in known_codes
                                             else "unrecognized_rpc_error")
            frozen = {"state": "unavailable", "domains": {},
                      "missing": ["first_hit_snapshot_read_failed"],
                      "diagnostic": diagnostic}
            from src.utils.logger import logger
            logger.warning("首击配置快照读取失败，继续录制逐击；本次配置标为不可用，诊断={}", diagnostic)
        return frozen

    def stop_capture(self):
        self._check()
        try:
            return self._client.stop_capture()
        except Exception:
            with self._owner._lock:
                if self._owner._lease is self:
                    self._owner._failed = True
            raise

    def get_battle_summary(self, **kwargs):
        self._check()
        return self._client.get_battle_summary(**kwargs)

    def get_battle_record(self, **kwargs):
        self._check()
        return self._client.get_battle_record(**kwargs)

    def get_battle_axis(self, **kwargs):
        self._check()
        return self._client.get_battle_axis(**kwargs)

    def abort(self):
        if not self._released and not self._aborted:
            self._aborted = True
            self._owner._abort_battle(self)

    def close(self):
        if self._released:
            return
        self._released = True
        try:
            for (event, _original), wrapped in tuple(self._handlers.items()):
                try:
                    self._client.remove_event_handler(event, wrapped)
                except Exception:
                    pass
            self._handlers.clear()
        finally:
            self._owner._release_battle(self)
