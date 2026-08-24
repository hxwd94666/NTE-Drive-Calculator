# 管理单个战斗抓包进程并发布不可变摘要。
"""Own one combat-profile nte-core process and publish immutable summaries."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol

from src.domain.battle_report import (
    BattleCaptureState,
    BattleSummary,
    BattleSummaryPersistenceOutcome,
    EMPTY_BATTLE_CAPTURE_STATE,
)
from src.integrations.nte_core_battle import (
    parse_battle_axis,
    parse_battle_record,
    parse_battle_summary,
    parse_battle_summary_event,
)
from src.integrations.nte_core import nte_core_error_has_domain_code
from src.observability import OperationContext
from src.observability.operation import log_event
from src.observability.redaction import safe_exception
from src.services.raw_capture_retention import prune_raw_capture_files


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
    ) -> BattleSummaryPersistenceOutcome: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class BattleCaptureService:
    """Qt-free lifecycle wrapper for a single live battle report session."""

    def __init__(
        self,
        *,
        client_factory: BattleClientFactory,
        operation_context: OperationContext,
        device_name: str | None = None,
        summary_writer: BattleSummaryWriter | None = None,
        raw_capture_enabled: bool = False,
        raw_capture_directory: str | Path | None = None,
    ) -> None:
        if raw_capture_enabled and raw_capture_directory is None:
            raise ValueError("启用战报原始抓包时必须提供账号抓包目录")
        self._client_factory = client_factory
        self._operation_context = operation_context
        self._device_name = device_name
        self._summary_writer = summary_writer
        self._raw_capture_enabled = bool(raw_capture_enabled)
        self._raw_capture_directory = (
            Path(raw_capture_directory).expanduser().resolve()
            if raw_capture_directory is not None
            else None
        )
        self._stop_event = threading.Event()
        self._lock = threading.RLock()
        self._handlers: list[BattleStateHandler] = []
        self._thread: threading.Thread | None = None
        self._client: BattleCoreClient | None = None
        self._state = EMPTY_BATTLE_CAPTURE_STATE
        self._latest_summary: BattleSummary | None = None
        self._last_sequence = -1
        self._event_error: Exception | None = None
        self._source_battle_record_id: str | None = None
        self._axis_cursor: str | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def state(self) -> BattleCaptureState:
        with self._lock:
            return self._state

    def add_state_handler(self, handler: BattleStateHandler) -> None:
        with self._lock:
            if handler not in self._handlers:
                self._handlers.append(handler)

    def remove_state_handler(self, handler: BattleStateHandler) -> None:
        with self._lock:
            if handler in self._handlers:
                self._handlers.remove(handler)

    def start(self) -> None:
        if self.is_running:
            return
        self._stop_event.clear()
        self._publish("starting", "正在启动 nte-core 战斗采集……", running=True)
        self._thread = threading.Thread(
            target=self._run,
            name="battle-capture-service",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        if not self.is_running:
            return
        self._publish(
            "stopping",
            "正在停止采集并读取最终战报……",
            running=True,
            summary=self._latest_summary,
        )
        self._stop_event.set()

    def close(self, *, timeout: float = 12.0) -> None:
        self.request_stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        captured_at_utc = _utc_now()
        log_event(
            "INFO",
            "battle_report.capture_started",
            "开始战报采集",
            self._operation_context,
            phase="started",
        )
        client: BattleCoreClient | None = None
        capture_started = False
        terminal_error: Exception | None = None
        persistence_outcome: BattleSummaryPersistenceOutcome | None = None
        final_payload_received = False
        capture_staged = False
        capture_finalized = False
        final_record: dict[str, Any] | None = None
        try:
            if self._raw_capture_enabled:
                assert self._raw_capture_directory is not None
                self._raw_capture_directory.mkdir(parents=True, exist_ok=True)
                self._prune_raw_captures()
            if self._summary_writer is not None:
                self._summary_writer.begin_capture(
                    capture_operation_id=self._operation_context.operation_id,
                    captured_at_utc=captured_at_utc,
                )
                capture_staged = True
            client = self._client_factory()
            self._client = client
            client.start()
            client.add_event_handler("event.battle.summary", self._on_summary_event)
            client.start_capture(
                profile="combat",
                device_name=self._device_name,
                include_incoming=True,
                server_damage_calibration=True,
                raw_capture=(
                    "enabled" if self._raw_capture_enabled else "disabled"
                ),
            )
            capture_started = True
            self._publish(
                "running",
                "采集中：进入战斗后将实时显示队伍伤害。",
                running=True,
            )
            while not self._stop_event.wait(0.5):
                self._poll_axis(client, maximum_pages=8)
            client.stop_capture()
            capture_started = False
            if self._event_error is not None:
                raise self._event_error
            final_record = self._poll_axis(client, maximum_pages=120)
            final_payload = (
                final_record.get("summary")
                if final_record is not None
                else client.get_battle_summary(subtract_time_stop=True)
            )
            if final_payload is not None:
                final_payload_received = True
                self._latest_summary = parse_battle_summary(
                    final_payload,
                    sequence=max(0, self._last_sequence + 1),
                )
                if self._summary_writer is not None:
                    persistence_outcome = self._summary_writer.finalize_summary(
                        raw_summary_payload=final_payload,
                        summary=self._latest_summary,
                        capture_operation_id=self._operation_context.operation_id,
                        captured_at_utc=captured_at_utc,
                        finalized_at_utc=_utc_now(),
                        raw_record_payload=final_record,
                    )
                    capture_finalized = True
        except Exception as error:
            terminal_error = error
        finally:
            if client is not None:
                try:
                    client.remove_event_handler(
                        "event.battle.summary", self._on_summary_event
                    )
                    if capture_started:
                        client.stop_capture()
                except Exception:
                    pass
                try:
                    client.close()
                except Exception as close_error:
                    if terminal_error is None:
                        terminal_error = close_error
            if self._raw_capture_enabled:
                self._prune_raw_captures()
            self._client = None
            if (
                capture_staged
                and not capture_finalized
                and self._summary_writer is not None
            ):
                try:
                    self._summary_writer.discard_capture(
                        capture_operation_id=self._operation_context.operation_id
                    )
                except Exception as discard_error:
                    if terminal_error is None:
                        terminal_error = discard_error
        summary = self._latest_summary
        if terminal_error is None:
            persistence_status = (
                persistence_outcome.status
                if persistence_outcome is not None
                else (
                    "final_summary_unavailable"
                    if self._summary_writer is not None and not final_payload_received
                    else "not_requested"
                )
            )
            record_id = (
                persistence_outcome.battle_record_id
                if persistence_outcome is not None
                else None
            )
            retention_kind = (
                persistence_outcome.retention_kind
                if persistence_outcome is not None
                else None
            )
            message = {
                "saved": "战报采集已结束并自动保存。",
                "skipped_empty": "战报采集已结束；没有有效伤害，未保存记录。",
                "discarded_stale": "账号上下文已变化，旧战报未保存。",
                "final_summary_unavailable": (
                    "战报采集已结束，但未取得最终摘要，未保存记录。"
                ),
            }.get(persistence_status, "战报采集已结束。")
            self._publish(
                "stopped",
                message,
                running=False,
                summary=summary,
                persistence_status=persistence_status,
                battle_record_id=record_id,
                retention_kind=retention_kind,
            )
            log_event(
                "INFO",
                "battle_report.capture_succeeded",
                "战报采集结束",
                self._operation_context,
                phase="succeeded",
                result="succeeded",
                character_count=len(summary.characters) if summary is not None else 0,
                skill_count=len(summary.skills) if summary is not None else 0,
                total_hits=summary.total_hits if summary is not None else 0,
                persistence_status=persistence_status,
                battle_record_id=record_id,
            )
        else:
            self._publish(
                "error",
                "战报采集失败。",
                running=False,
                summary=summary,
                error=str(terminal_error),
                error_code=type(terminal_error).__name__,
            )
            log_event(
                "ERROR",
                "battle_report.capture_failed",
                "战报采集失败",
                self._operation_context,
                phase="failed",
                result="failed",
                error=safe_exception(terminal_error),
            )

    def _prune_raw_captures(self) -> None:
        """Best-effort cleanup without exposing the account log path."""
        directory = self._raw_capture_directory
        if directory is None:
            return
        try:
            result = prune_raw_capture_files(directory)
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.raw_capture_prune_failed",
                "清理战报原始抓包失败，将在下次采集时重试",
                self._operation_context,
                error=safe_exception(error),
            )
            return
        if result.deleted_count:
            log_event(
                "INFO",
                "battle_report.raw_capture_pruned",
                "已清理旧战报原始抓包",
                self._operation_context,
                deleted_count=result.deleted_count,
                deleted_bytes=result.deleted_bytes,
                retained_count=result.retained_count,
            )

    def _poll_axis(
        self,
        client: BattleCoreClient,
        *,
        maximum_pages: int,
    ) -> dict[str, Any] | None:
        raw_record = client.get_battle_record(
            battle_record_id=self._source_battle_record_id,
            subtract_time_stop=True,
        )
        if raw_record is None:
            return None
        record = parse_battle_record(raw_record)
        source_record_id = str(record["battle_record_id"])
        if self._source_battle_record_id is None:
            self._source_battle_record_id = source_record_id
        elif source_record_id != self._source_battle_record_id:
            raise RuntimeError("同一次采集出现了不同的 nte-core 战斗记录")

        for _page_index in range(maximum_pages):
            try:
                raw_page = client.get_battle_axis(
                    battle_record_id=source_record_id,
                    cursor=self._axis_cursor,
                    limit=500,
                )
            except Exception as error:
                if nte_core_error_has_domain_code(
                    error,
                    frozenset({"BATTLE_AXIS_CURSOR_EXPIRED"}),
                ):
                    self._axis_cursor = None
                    continue
                raise
            if raw_page is None:
                break
            page = parse_battle_axis(raw_page)
            writer = self._summary_writer
            if writer is not None:
                writer.append_axis_page(
                    capture_operation_id=self._operation_context.operation_id,
                    page=page,
                )
            next_cursor = page.get("next_cursor")
            if next_cursor is None or next_cursor == self._axis_cursor:
                break
            self._axis_cursor = str(next_cursor)
            if not page["rows"]:
                break
        return record

    def _on_summary_event(self, event: dict[str, object]) -> None:
        try:
            summary = parse_battle_summary_event(event)
        except Exception as error:
            self._event_error = error
            self._stop_event.set()
            return
        with self._lock:
            if summary.sequence and summary.sequence <= self._last_sequence:
                return
            self._last_sequence = max(self._last_sequence, summary.sequence)
            self._latest_summary = summary
        current_phase = self.state.phase
        self._publish(
            "stopping" if current_phase == "stopping" else "running",
            (
                "正在停止采集并读取最终战报……"
                if current_phase == "stopping"
                else "采集中：已收到实时伤害数据。"
            ),
            running=True,
            summary=summary,
        )

    def _publish(
        self,
        phase: str,
        message: str,
        *,
        running: bool,
        summary: BattleSummary | None = None,
        error: str | None = None,
        error_code: str | None = None,
        persistence_status: str = "not_requested",
        battle_record_id: int | None = None,
        retention_kind: Literal["auto", "manual"] | None = None,
    ) -> None:
        state = BattleCaptureState(
            phase=phase,
            message=message,
            running=running,
            summary=summary,
            error=error,
            error_code=error_code,
            persistence_status=persistence_status,
            battle_record_id=battle_record_id,
            retention_kind=retention_kind,
        )
        with self._lock:
            self._state = state
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                handler(state)
            except Exception:
                # UI observers must not terminate the capture lifecycle.
                continue
