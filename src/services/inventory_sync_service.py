# 在应用生命周期内持续接收、稳定并保存本地核心组件的背包快照。
"""在应用生命周期内持续接收、稳定并保存 nte-core 背包快照。"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from src.integrations.nte_core import NteCoreClient
from src.services.account_settings_service import AccountSettingsService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.logger import logger

from .inventory_snapshot_stabilizer import InventorySnapshotStabilizer


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
    updated_at_utc: str = ""


StateHandler = Callable[[InventorySyncState], None]


class _CoreClient(Protocol):
    hello_result: dict[str, Any] | None

    def start(self) -> Any: ...
    def add_event_handler(self, method: str | None, handler: Callable[[dict[str, Any]], None]) -> None: ...
    def remove_event_handler(self, method: str | None, handler: Callable[[dict[str, Any]], None]) -> None: ...
    def start_capture(self, **kwargs: Any) -> Mapping[str, Any]: ...
    def stop_capture(self) -> Mapping[str, Any]: ...
    def equip_one_key(self, **kwargs: Any) -> Any: ...
    def set_item_discarded(self, **kwargs: Any) -> Any: ...
    def set_item_locked(self, **kwargs: Any) -> Any: ...
    def close(self) -> None: ...


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
        client_factory: Callable[[], _CoreClient] = NteCoreClient,
        dao_factory: Callable[..., UserDataDao] = UserDataDao,
        settle_seconds: float | None = None,
        capture_device_id: str | None = None,
        raw_capture_enabled: bool | None = None,
        poll_seconds: float = 0.05,
        template_refresh: Callable[[], Any] | None = None,
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
        self._poll_seconds = poll_seconds
        self._template_refresh = template_refresh

        self._state = InventorySyncState(updated_at_utc=_utc_now())
        self._state_condition = threading.Condition()
        self._handlers: list[StateHandler] = []
        self._handlers_lock = threading.Lock()
        self._event_lock = threading.Lock()
        self._latest_inventory_event: dict[str, Any] | None = None
        self._event_ready = threading.Event()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._client: _CoreClient | None = None

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

    def set_item_discarded(self, *, equipment: Mapping[str, Any], discarded: bool) -> Any:
        """复用持续运行的核心进程更新单件装备的弃置状态。"""
        client = self._equipment_client()
        return client.set_item_discarded(equipment=equipment, discarded=discarded)

    def set_item_locked(self, *, equipment: Mapping[str, Any], locked: bool) -> Any:
        """复用持续运行的核心进程更新单件装备的锁定状态。"""
        client = self._equipment_client()
        return client.set_item_locked(equipment=equipment, locked=locked)

    def _equipment_client(self) -> _CoreClient:
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
        self._stop_requested.clear()
        self._event_ready.clear()
        with self._event_lock:
            self._latest_inventory_event = None
        self._publish(
            "starting",
            "正在启动背包同步服务",
            running=True,
            capturing=False,
            error=None,
        )
        self._thread = threading.Thread(
            target=self._run,
            name="inventory-sync-service",
            daemon=True,
        )
        self._thread.start()

    def _on_inventory_event(self, event: dict[str, Any]) -> None:
        # 单槽合并：完整快照描述的是某一时刻的全部背包，积压时只需处理最新版本。
        with self._event_lock:
            self._latest_inventory_event = dict(event)
        self._event_ready.set()

    def _take_latest_event(self) -> dict[str, Any] | None:
        with self._event_lock:
            event = self._latest_inventory_event
            self._latest_inventory_event = None
            self._event_ready.clear()
            return event

    def _open_dao(self) -> UserDataDao:
        kwargs: dict[str, Any] = {}
        if not self.database_path.is_file():
            kwargs = {
                "account_id": self.account_id,
                "account_name": self.account_name,
            }
        return self._dao_factory(self.database_path, **kwargs)

    @staticmethod
    def _protocol_version(client: _CoreClient) -> int | None:
        hello = client.hello_result
        if not isinstance(hello, Mapping):
            return None
        value = hello.get("protocol_version")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def _run(self) -> None:
        client: _CoreClient | None = None
        fatal_error: Exception | None = None
        try:
            with self._open_dao() as dao:
                settings = AccountSettingsService(self.database_path).load("sync")
                settle_seconds = (
                    self._settle_seconds
                    if self._settle_seconds is not None
                    else float(settings["inventory_settle_seconds"])
                )
                stabilizer = InventorySnapshotStabilizer(settle_seconds)
                current_id = dao.current_inventory_snapshot_id()
                if current_id is not None:
                    previous = dao.raw_snapshot(current_id)
                    if previous:
                        try:
                            stabilizer.seed_committed(previous)
                        except ValueError:
                            pass

                client = self._client_factory()
                self._client = client
                client.start()
                client.add_event_handler("event.inventory.snapshot", self._on_inventory_event)
                capture_device = self._capture_device_id
                if capture_device is None:
                    capture_device = settings.get("capture_device_id")
                raw_enabled = self._raw_capture_enabled
                if raw_enabled is None:
                    raw_enabled = bool(settings.get("raw_capture_enabled"))
                client.start_capture(
                    profile="inventory",
                    device_name=capture_device,
                    raw_capture="enabled" if raw_enabled else "disabled",
                )
                current_summary = dao.current_inventory_summary()
                self._publish(
                    "waiting" if current_summary is None else "listening",
                    "等待进入游戏并接收完整背包"
                    if current_summary is None
                    else "背包已同步，正在后台监听变化",
                    running=True,
                    capturing=True,
                    last_snapshot_id=current_id,
                    last_item_count=(
                        int(current_summary["stored_item_count"])
                        if current_summary is not None
                        else None
                    ),
                )

                retry_save_at = 0.0
                while not self._stop_requested.is_set():
                    self._event_ready.wait(self._poll_seconds)
                    event = self._take_latest_event()
                    if event is not None:
                        result = stabilizer.offer(event)
                        if result.status in {"collecting", "changed"}:
                            self._publish(
                                "collecting",
                                f"已接收 {result.item_count} 件，等待背包内容稳定",
                                running=True,
                                capturing=True,
                                pending_item_count=result.item_count,
                                added_count=result.added_count,
                                removed_count=result.removed_count,
                                error=None,
                            )
                        elif result.status == "reverted":
                            self._publish(
                                "listening",
                                "背包变化已撤销，继续后台监听",
                                running=True,
                                capturing=True,
                                pending_item_count=None,
                                added_count=0,
                                removed_count=0,
                            )

                    now = time.monotonic()
                    stable = stabilizer.ready(now=now)
                    if stable is None or now < retry_save_at:
                        continue
                    self._publish(
                        "saving",
                        f"背包已稳定，正在保存 {stable.item_count} 件",
                        running=True,
                        capturing=True,
                        pending_item_count=stable.item_count,
                    )
                    try:
                        snapshot_id = dao.import_inventory_snapshot(
                            stable.message,
                            source="nte_core",
                            protocol_version=self._protocol_version(client),
                        )
                    except Exception as exc:
                        retry_save_at = time.monotonic() + 2.0
                        self._publish(
                            "error",
                            "保存稳定背包失败，后台将自动重试",
                            running=True,
                            capturing=True,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        continue
                    stabilizer.mark_committed(stable.fingerprint)
                    if self._template_refresh is not None:
                        try:
                            refreshed = self._template_refresh()
                            if isinstance(refreshed, Mapping) and refreshed.get("changed"):
                                logger.info(
                                    "已刷新公共角色/弧盘模板："
                                    f"{refreshed.get('role_count', 0)} 名角色，"
                                    f"{refreshed.get('fork_count', 0)} 个弧盘"
                                )
                        except Exception as exc:
                            # 背包快照已经成功提交，模板缓存刷新不能阻断同步监听。
                            logger.warning(f"公共角色/弧盘模板刷新失败，将在下次背包同步时重试：{exc}")
                    try:
                        retention = dao.prune_inventory_snapshots()
                        if retention["deleted_snapshot_count"]:
                            logger.info(
                                "已按保留策略清理 "
                                f"{retention['deleted_snapshot_count']} 份历史背包快照；"
                                f"当前共 {retention['total_after']} 份"
                            )
                    except Exception as exc:
                        # 新快照已经安全提交，清理失败不能让同步服务重新导入同一份数据。
                        logger.warning(f"历史背包快照清理失败，将在下次同步或手动维护时重试：{exc}")
                    retry_save_at = 0.0
                    self._publish(
                        "listening",
                        "背包同步完成，正在后台监听变化",
                        running=True,
                        capturing=True,
                        pending_item_count=None,
                        added_count=0,
                        removed_count=0,
                        last_snapshot_id=snapshot_id,
                        last_item_count=stable.item_count,
                        last_synced_at_utc=_utc_now(),
                        error=None,
                    )
        except Exception as exc:
            fatal_error = exc
            self._publish(
                "error",
                "背包同步服务已停止",
                running=False,
                capturing=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if client is not None:
                try:
                    client.remove_event_handler("event.inventory.snapshot", self._on_inventory_event)
                except Exception:
                    pass
                try:
                    client.stop_capture()
                except Exception:
                    pass
                try:
                    client.close()
                except Exception:
                    pass
            self._client = None
            if fatal_error is None:
                self._publish(
                    "stopped",
                    "背包同步已停止",
                    running=False,
                    capturing=False,
                    pending_item_count=None,
                )

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
