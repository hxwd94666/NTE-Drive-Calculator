# 声明背包同步服务实际使用的 nte-core 客户端窄协议。
"""Narrow nte-core client contract consumed by inventory synchronization."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol


class InventoryCoreClient(Protocol):
    hello_result: dict[str, Any] | None

    def start(self) -> Any: ...
    def add_event_handler(
        self,
        method: str | None,
        handler: Callable[[dict[str, Any]], None],
    ) -> None: ...
    def remove_event_handler(
        self,
        method: str | None,
        handler: Callable[[dict[str, Any]], None],
    ) -> None: ...
    def start_capture(
        self,
        *,
        profile: Literal["inventory", "combat"],
        device_name: str | None = None,
        include_incoming: bool = True,
        server_damage_calibration: bool = True,
        raw_capture: Literal["enabled", "disabled"] = "disabled",
    ) -> Mapping[str, Any]: ...
    def stop_capture(self) -> Mapping[str, Any]: ...
    def equip_one_key(
        self,
        *,
        character: Mapping[str, Any],
        placements: Sequence[Mapping[str, Any]],
        core: Mapping[str, Any],
        timeout: float | None = None,
    ) -> Mapping[str, Any]: ...
    def equip_module(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
        row: int,
        column: int,
    ) -> Mapping[str, Any]: ...
    def unequip_module(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...
    def unequip_core(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...
    def unequip_all(
        self,
        *,
        character: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...
    def move_module_to_character(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
        row: int,
        column: int,
    ) -> Mapping[str, Any]: ...
    def set_item_discarded(
        self,
        *,
        equipment: Mapping[str, Any],
        discarded: bool,
    ) -> Mapping[str, Any]: ...
    def set_item_locked(
        self,
        *,
        equipment: Mapping[str, Any],
        locked: bool,
    ) -> Mapping[str, Any]: ...
    def close(self) -> None: ...
