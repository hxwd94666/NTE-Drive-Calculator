# 共享同一连接的在途状态读取；只读检测不夺取业务租约，迟到结果复核原连接。
from dataclasses import dataclass, field
from threading import Event, Lock
from src.integrations.nte_core_protocol import NteCoreRpcError, NteCoreTimeoutError
from src.observability import OperationContext, log_event
from src.integrations.native_inventory_snapshot import NativeSnapshotPending
from src.services.inventory.sync.capture_wait import InventorySyncCancelled
from typing import Any
from src.integrations.native_raw_snapshot import read_native_raw_domain


@dataclass
class _StatusRead:
    client: object
    done: Event = field(default_factory=Event)
    result: object = None
    error: BaseException | None = None


class NativeStatusQueries:
    def __init__(self):
        self._lock = Lock()
        self._pending = None

    def read(self, client, *, timeout=2.0):
        with self._lock:
            pending = self._pending
            owner = pending is None or pending.client is not client
            if owner:
                pending = self._pending = _StatusRead(client)
        if owner:
            try:
                pending.result = client.call('core.status', timeout=timeout)
            except BaseException as error:
                pending.error = error
            finally:
                with self._lock:
                    pending.done.set()
                    if self._pending is pending:
                        self._pending = None
        elif not pending.done.wait(timeout):
            raise NteCoreTimeoutError('core.status', timeout)
        if pending.error is not None:
            raise pending.error
        return pending.result


def transient_inspection_error(error):
    if isinstance(error, (NativeSnapshotPending, InventorySyncCancelled)):
        return True
    return isinstance(error, NteCoreRpcError) and (
        (error.code == -32000 and error.domain_code == 'REQUEST_IN_PROGRESS')
        or (error.code == -32001 and error.message in {
            'not_ready', 'source_changed', 'control_busy', 'control_timeout', 'snapshot_not_found', 'disabled',
        })
    )


def log_inspection_failure(error, *, retained):
    # 只记录白名单原因和状态，不抄服务器正文、请求载荷或诊断原文。
    domain = getattr(error, 'domain_code', None)
    log_event('WARNING', 'native_session.inspection_failed',
              '原生检测未完成，保留共享连接' if retained else '原生检测失败，关闭失效连接',
              OperationContext.create('native_session'),
              error_type=type(error).__name__, rpc_code=getattr(error, 'code', None),
              reason=domain if domain in {'REQUEST_IN_PROGRESS', 'NATIVE_SNAPSHOT_INCOMPLETE',
                                         'NATIVE_MAPPING_UNSUPPORTED', 'NATIVE_CAPABILITY_MISSING'} else 'other',
              connection_retained=retained)


def _inspect_snapshot(self, *, refresh: bool = False, check_equipment: bool = True) -> dict[str, Any]:
    from src.services.native_session.session import SNAPSHOT_DOMAINS, NATIVE_EQUIPMENT_CAPABILITIES
    with self._lock:
        self._check_inspection()
        client = self._connect()
    try:
        capabilities = (client.hello_result or {}).get("capabilities", [])
        # 状态 RPC 不持有战报停止/超时 abort 所需的 owner 锁。
        status = self._status_queries.read(client)
        self._runtime_cost_log.observe(status)
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
                                from src.integrations.native_battle_snapshot import validate_native_snapshot_current
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
                            # 仅这些明确提供方拒绝使当前域无效，不破坏其他域或共享连接。
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
    except Exception as error:
        retained = transient_inspection_error(error)
        log_inspection_failure(error, retained=retained)
        if retained:
            raise
        with self._lock:
            if self._client is client:
                self._failed = True
                if self._lease is None:
                    self._finish_close()
        raise
