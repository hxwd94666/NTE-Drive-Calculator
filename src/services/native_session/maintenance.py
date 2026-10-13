# 为共享原生会话预留空闲维护窗口，不取消其他功能的业务租约。
from contextlib import contextmanager
from time import monotonic, sleep

from src.integrations.nte_core_protocol import NteCoreError


class NativeSessionMaintenance:
    """持有临时连接维护门禁，不改各功能已保存的偏好。"""

    def __init__(self, owner):
        self.owner = owner
        self.closed = False
        if not owner._lock.acquire(blocking=False):
            raise NteCoreError('原生请求正在收尾，请稍后更新。')
        try:
            if owner._maintenance.is_set():
                raise NteCoreError('组件已有维护操作，不能重复更新。')
            if owner._lease is not None or owner._battle_requested.is_set():
                raise NteCoreError('战报正在录制或收尾，请停止录制并等待保存完成，再更新插件。')
            inventory = owner._inventory_lease
            if inventory is not None and inventory.maintenance_blocked:
                raise NteCoreError(inventory.maintenance_description)
            owner._maintenance.set()
        finally:
            owner._lock.release()

    def disconnect(self, *, check, timeout=15):
        """业务 owner 已请求停止后由更新 worker 调用；等待真实租约释放再断开。"""
        owner, deadline = self.owner, monotonic() + timeout
        while monotonic() < deadline:
            check()
            if owner._snapshot_lock.acquire(timeout=.1):
                try:
                    with owner._lock:
                        if owner._lease is not None:
                            raise NteCoreError('战报仍占用连接，未执行更新。')
                        if owner._inventory_lease is None and owner._refresh_active is None:
                            owner._finish_close()
                            owner._hud_applied = None
                            return
                finally:
                    owner._snapshot_lock.release()
            sleep(.05)
        raise NteCoreError('原生任务尚未完成收尾，未执行插件更新。')

    def close(self):
        if not self.closed:
            self.closed = True
            self.owner._maintenance.clear()


def reserve_plugin_maintenance(owner):
    return NativeSessionMaintenance(owner)


@contextmanager
def plugin_maintenance(owner):
    if not owner._snapshot_lock.acquire(blocking=False):
        raise NteCoreError('原生读取仍在进行，请结束同步后更新组件。')
    try:
        with owner._lock:
            if (owner._maintenance.is_set() or owner._lease is not None or owner._inventory_lease is not None
                    or owner._refresh_active is not None or owner._battle_requested.is_set()
                    or owner._performance_active or owner._performance_owner is not None or owner._performance_trace_active or (owner._hud_applied and any(owner._hud_applied[1].values()))):
                raise NteCoreError('请先停止同步、战报、HUD 和性能显示，再更新组件。')
            owner._maintenance.set()
            try:
                owner._finish_close()
            except Exception:
                owner._maintenance.clear()
                raise
    finally:
        owner._snapshot_lock.release()
    try:
        yield
    finally:
        owner._maintenance.clear()
