# 定义战报采集客户端、状态回调和持久化端的窄接口。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal, Protocol

from src.domain.battle_report import BattleCaptureState, BattleSummary, BattleSummaryPersistenceOutcome


BattleStateHandler = Callable[[BattleCaptureState], None]

class BattleCoreClient(Protocol):
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
        wait_for_game: bool = False,
    ) -> Mapping[str, Any]: ...

    def stop_capture(self) -> Mapping[str, Any]: ...

    def get_battle_summary(
        self, *, subtract_time_stop: bool = True
    ) -> Mapping[str, Any] | None: ...

    def get_battle_record(
        self,
        *,
        battle_record_id: str | None = None,
        subtract_time_stop: bool = True,
    ) -> Mapping[str, Any] | None: ...

    def get_battle_axis(
        self,
        *,
        battle_record_id: str,
        cursor: str | None = None,
        limit: int = 500,
    ) -> Mapping[str, Any] | None: ...

    def close(self) -> None: ...


BattleClientFactory = Callable[[], BattleCoreClient]

class BattleSummaryWriter(Protocol):
    def bind_runtime_snapshot(self, *, capture_operation_id: str, snapshot: Mapping[str, Any]) -> None: ...

    def begin_capture(
        self,
        *,
        capture_operation_id: str,
        captured_at_utc: str,
    ) -> None: ...

    def append_axis_page(
        self,
        *,
        capture_operation_id: str,
        page: Mapping[str, Any],
    ) -> None: ...

    def replace_axis_pages(
        self,
        *,
        capture_operation_id: str,
        pages: Sequence[Mapping[str, Any]],
        source_generation: str,
        incomplete_reason: str | None = None,
    ) -> None: ...

    def discard_capture(self, *, capture_operation_id: str) -> None: ...

    def finalize_summary(
        self,
        *,
        raw_summary_payload: Mapping[str, Any],
        summary: BattleSummary,
        capture_operation_id: str,
        captured_at_utc: str,
        finalized_at_utc: str,
        raw_record_payload: Mapping[str, Any] | None = None,
        nte_core_provenance: Mapping[str, Any] | None = None,
    ) -> BattleSummaryPersistenceOutcome: ...

