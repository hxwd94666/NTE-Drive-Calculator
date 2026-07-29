# 通过已运行的 nte-core 会话写回仓库装备的锁定与弃置状态。
"""nte-core adapter for warehouse equipment state changes."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class WarehouseStateWriteError(RuntimeError):
    """The live core session cannot safely accept a warehouse state change."""


class LiveInventorySync(Protocol):
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


class WarehouseStateWriter:
    """Validate and send state RPCs without owning snapshot or UI policy."""

    def __init__(self, sync_service: LiveInventorySync) -> None:
        self.sync_service = sync_service

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
        if target_state not in {"normal", "locked", "discarded"}:
            raise WarehouseStateWriteError(f"未知目标状态：{target_state}")
        discarded = bool(row.get("discarded"))
        locked = bool(row.get("locked"))
        if target_state == "normal":
            if discarded:
                self.sync_service.set_item_discarded(
                    equipment=equipment,
                    discarded=False,
                )
            if locked:
                self.sync_service.set_item_locked(
                    equipment=equipment,
                    locked=False,
                )
        elif target_state == "locked":
            if discarded:
                self.sync_service.set_item_discarded(
                    equipment=equipment,
                    discarded=False,
                )
            if not locked:
                self.sync_service.set_item_locked(
                    equipment=equipment,
                    locked=True,
                )
        else:
            if locked:
                self.sync_service.set_item_locked(
                    equipment=equipment,
                    locked=False,
                )
            if not discarded:
                self.sync_service.set_item_discarded(
                    equipment=equipment,
                    discarded=True,
                )
