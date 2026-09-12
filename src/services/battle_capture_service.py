# 管理单个战斗抓包进程并发布不可变摘要。
from __future__ import annotations

import threading
import time
from concurrent.futures import CancelledError
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

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
from src.integrations.operation_guard import OperationGuard, require_operation
from src.observability import OperationContext
from src.observability.operation import log_event
from src.observability.redaction import safe_exception
from src.services.raw_capture_retention import prune_battle_raw_captures
from src.domain.battle_summary_observation import has_active_battle_observation
from src.services.native_battle_scopes import observe_native_scopes
from src.services.battle_capture_metadata import (
    freeze_nte_core_provenance,
    native_capture_end_warning,
    native_capture_end_reason,
    with_comparison_metadata,
)
from src.services.battle_capture_lifecycle import stop_capture_with_timeout
from src.services.battle_capture_start import start_battle_capture, supports_packet_wait
from src.services.battle_capture_contracts import BattleCoreClient, BattleClientFactory, BattleStateHandler, BattleSummaryWriter
from src.services.battle_capture_polling import poll_battle_until_stopped


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


_BATTLE_READ_CONTRACT_VERSION = 5

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
        stop_timeout_seconds: float = 12.0,
        required_source: Literal["native", "packet"] | None = None,
        comparison_id: str | None = None,
        operation_guard: OperationGuard | None = None,
    ) -> None:
        if raw_capture_enabled and raw_capture_directory is None:
            raise ValueError("启用战报原始抓包时必须提供账号抓包目录")
        if stop_timeout_seconds <= 0:
            raise ValueError("战报停止超时必须大于 0")
        if comparison_id is not None and (
            not comparison_id.strip() or required_source not in {"native", "packet"}
        ):
            raise ValueError("双路配对需要有效标识和明确采集来源")
        self._client_factory = client_factory
        self._operation_guard = operation_guard
        self._required_source = required_source
        self._comparison_id = comparison_id
        self._operation_context = operation_context
        self._device_name = device_name
        self._summary_writer = summary_writer
        self._raw_capture_enabled = bool(raw_capture_enabled)
        self._raw_capture_directory = Path(raw_capture_directory).expanduser().resolve() if raw_capture_directory is not None else None
        self._stop_timeout_seconds = float(stop_timeout_seconds)
        self._stop_event = threading.Event()
        self._summary_event = threading.Event()
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
        self._discard_requested = False
        self._native_terminal: dict[str, Any] | None = None
        self._requested_end_reason: Literal["scene_transition"] | None = None
        self._packet_capture_waiting = False

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
        self._require_start_permission()
        if self.is_running:
            return
        self._stop_event.clear()
        self._summary_event.clear()
        with self._lock:
            self._discard_requested = False
            self._native_terminal = None
            self._requested_end_reason = None
            self._latest_summary, self._event_error = None, None
            self._source_battle_record_id, self._axis_cursor = None, None
            self._last_sequence = -1
        self._publish("starting", "正在启动 nte-core 战斗采集……", running=True)
        self._thread = threading.Thread(
            target=self._run,
            name="battle-capture-service",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self, *, end_reason: Literal["scene_transition"] | None = None) -> None:
        if not self.is_running:
            return
        with self._lock:
            if self._discard_requested:
                return
            if end_reason is not None:
                self._requested_end_reason = end_reason
        self._publish(
            "stopping",
            "正在停止采集并读取最终战报……",
            running=True,
            summary=self._latest_summary,
        )
        self._stop_event.set()

    def request_discard(self) -> None:
        """Stop this capture and delete its staged rows without finalizing it."""

        if not self.is_running:
            return
        with self._lock:
            self._discard_requested = True
        self._publish(
            "stopping",
            "正在放弃当前战报并准备重新采集……",
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
        start_requested_at = time.monotonic()
        log_event(
            "INFO",
            "battle_report.capture_starting",
            "正在准备战报采集连接",
            self._operation_context,
            phase="starting", source=self._required_source or "undetermined",
        )
        client: BattleCoreClient | None = None
        capture_started = False
        terminal_error: Exception | None = None
        persistence_outcome: BattleSummaryPersistenceOutcome | None = None
        final_payload_received = False
        capture_staged = False
        capture_finalized = False
        empty_capture_discarded = False
        final_payload: Mapping[str, Any] | None = None
        final_record: dict[str, Any] | None = None
        nte_core_provenance: dict[str, Any] | None = None
        try:
            self._require_start_permission()
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
            self._require_start_permission()
            client = self._client_factory()
            self._client = client
            self._require_start_permission()
            client.start()
            source = "native" if getattr(client, "native_capture", False) else "packet"
            if self._required_source is not None and source != self._required_source:
                raise RuntimeError("双路对照采集来源不符；请确认采集 DLL 已加载，不能自动切换来源。")
            nte_core_provenance = freeze_nte_core_provenance(client)
            self._packet_capture_waiting = supports_packet_wait(client, source)
            client.add_event_handler("event.battle.summary", self._on_summary_event)
            client.add_event_handler("event.capture.status", self._on_capture_status)
            capture_started = start_battle_capture(
                client, source=source, require_start=self._require_start_permission,
                stop_event=self._stop_event, stop_timeout_seconds=self._stop_timeout_seconds,
                device_name=self._device_name, raw_capture_enabled=self._raw_capture_enabled,
                summary_writer=self._summary_writer, capture_operation_id=self._operation_context.operation_id,
                on_wait=lambda message: self._publish(
                    "starting", message, running=True,
                ),
            )
            self._packet_capture_waiting = not capture_started
            if capture_started:
                log_event("INFO", "battle_report.capture_started", "战报采集已就绪，开始监听",
                          self._operation_context, phase="running", source=source,
                          duration_ms=round((time.monotonic() - start_requested_at) * 1000, 2))
                if not self._stop_event.is_set():
                    self._publish(
                        "running",
                        ("增强采集中：DLL 逐击与 Buff；伤害覆盖、时停尚未完整验证。"
                         if getattr(client, "native_capture", False)
                         else "抓包采集中：进入战斗后将实时显示队伍伤害。"),
                        running=True,
                    )
                poll_battle_until_stopped(
                    lambda: self._poll_axis(client, maximum_pages=8),
                    stop_event=self._stop_event, operation=self._operation_context,
                    notify=lambda message: self._publish("running", message, running=True, summary=self._latest_summary),
                )
                stopped = self._stop_client_capture(client)
                capture_started = False
                native = bool(getattr(client, "native_capture", False))
                if native:
                    self._native_terminal = {**(self._native_terminal or {}), **stopped}
                if (
                    not native and not self._discard_was_requested()
                    and self._event_error is None
                    and not self._has_observed_battle_evidence()
                ):
                    self._summary_event.wait(0.25)
                if self._discard_was_requested():
                    pass
                elif self._event_error is not None:
                    raise self._event_error
                elif not native and not self._has_observed_battle_evidence():
                    empty_capture_discarded = True
                else:
                    final_record = self._read_final_axis(client)
                    if self._comparison_id is not None and final_record is None:
                        raise RuntimeError("双路对照缺少最终战斗记录，无法保存可追溯的配对战报。")
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
                    if self._latest_summary.total_damage <= 0 and (
                        self._latest_summary.total_hits <= 0
                        or self._end_reason(final_record) == "scene_transition"
                    ):
                        empty_capture_discarded = True
                    elif self._summary_writer is not None:
                        persistence_outcome = self._summary_writer.finalize_summary(
                            raw_summary_payload=final_payload,
                            summary=self._latest_summary,
                            capture_operation_id=self._operation_context.operation_id,
                            captured_at_utc=captured_at_utc,
                            finalized_at_utc=_utc_now(),
                            raw_record_payload=with_comparison_metadata(
                                final_record, comparison_id=self._comparison_id,
                                source=self._required_source,
                            ),
                            nte_core_provenance=nte_core_provenance,
                        )
                        capture_finalized = True
            else:
                empty_capture_discarded = True
        except Exception as error:
            if isinstance(error, CancelledError) and self._stop_event.is_set() and client is None:
                empty_capture_discarded = True
            else:
                terminal_error = error
        finally:
            if client is not None:
                try:
                    client.remove_event_handler(
                        "event.battle.summary", self._on_summary_event
                    )
                    client.remove_event_handler("event.capture.status", self._on_capture_status)
                    if capture_started:
                        self._stop_client_capture(client)
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
        discarded = self._discard_was_requested()
        if terminal_error is None and discarded:
            self._publish(
                "stopped",
                "当前战报已放弃，正在重新开始采集。",
                running=False,
                persistence_status="discarded_restart",
            )
            log_event(
                "INFO",
                "battle_report.capture_discarded",
                "当前战报已放弃",
                self._operation_context,
                phase="discarded",
                result="discarded",
            )
        elif terminal_error is None:
            persistence_status = (
                persistence_outcome.status
                if persistence_outcome is not None
                else (
                    "skipped_empty"
                    if empty_capture_discarded
                    else (
                        "final_summary_unavailable"
                        if self._summary_writer is not None
                        and not final_payload_received
                        else "not_requested"
                    )
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
            if persistence_outcome is not None and persistence_outcome.warning_message:
                message += persistence_outcome.warning_message
            native_warning = native_capture_end_warning(self._native_terminal, final_record)
            message += native_warning
            self._publish(
                "stopped",
                message,
                running=False,
                summary=summary,
                persistence_status=persistence_status,
                battle_record_id=record_id,
                retention_kind=retention_kind,
                end_reason=self._end_reason(final_record),
            )
            log_event(
                "WARNING" if native_warning else "INFO",
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
                capture_warning=native_warning or None,
            )
        else:
            self._publish(
                "error",
                "战报采集失败。",
                running=False,
                summary=summary,
                error=str(terminal_error),
                error_code=getattr(terminal_error, "domain_code", type(terminal_error).__name__),
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

    def _require_start_permission(self) -> None:
        if self._required_source not in {"native", "packet"}:
            raise ValueError("战报采集需要明确选择 DLL 或抓包来源。")
        capability = "native_battle" if self._required_source == "native" else "packet_capture"
        require_operation(self._operation_guard, capability)
        if self._raw_capture_enabled:
            require_operation(self._operation_guard, "diagnostics")

    def _discard_was_requested(self) -> bool:
        with self._lock:
            return self._discard_requested

    def _end_reason(self, record: Mapping[str, Any] | None = None) -> str | None:
        reason = native_capture_end_reason(self._native_terminal, record)
        return reason if reason not in {None, "user_stop"} else self._requested_end_reason or reason

    def _has_observed_battle_evidence(self) -> bool:
        with self._lock:
            summary = self._latest_summary
            return self._source_battle_record_id is not None or bool(
                summary is not None
                and (summary.total_damage > 0 or summary.total_hits > 0)
            )

    def _stop_client_capture(self, client: BattleCoreClient) -> Mapping[str, Any]:
        return stop_capture_with_timeout(client, self._stop_timeout_seconds)

    def _prune_raw_captures(self) -> None:
        prune_battle_raw_captures(self._raw_capture_directory, self._operation_context)

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
        self._require_contract_v5(record)
        observe_native_scopes(client, self._summary_writer, self._operation_context.operation_id, record, stop_requested=self._stop_event.is_set)
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
            self._require_contract_v5(page)
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

    def _read_final_axis(
        self,
        client: BattleCoreClient,
    ) -> dict[str, Any] | None:
        raw_record = client.get_battle_record(
            battle_record_id=self._source_battle_record_id,
            subtract_time_stop=True,
        )
        if raw_record is None:
            return None
        record = parse_battle_record(raw_record)
        self._require_contract_v5(record)
        observe_native_scopes(client, self._summary_writer, self._operation_context.operation_id, record, final=True)
        source_record_id = str(record["battle_record_id"])
        generation = str(record["generation"])
        incomplete_reason: str | None = None
        pages: list[dict[str, Any]] = []

        if str(record.get("state") or "") != "finalized":
            incomplete_reason = "final_record_not_finalized"
        else:
            cursor: str | None = None
            # Native rows carry Buff snapshots, so its byte-bounded pages can
            # contain fewer than 500 hits. Allow its 128 MiB retained stream.
            page_limit = 1024 if record.get("source") == "native_dll" else 120
            for _page_index in range(page_limit):
                try:
                    raw_page = client.get_battle_axis(
                        battle_record_id=source_record_id,
                        cursor=cursor,
                        limit=500,
                    )
                except Exception as error:
                    if nte_core_error_has_domain_code(
                        error,
                        frozenset({"BATTLE_AXIS_CURSOR_EXPIRED"}),
                    ):
                        incomplete_reason = "final_axis_cursor_expired"
                        break
                    raise
                if raw_page is None:
                    break
                page = parse_battle_axis(raw_page)
                self._require_contract_v5(page)
                if (
                    str(page["generation"]) != generation
                    or str(page["battle_record_id"]) != source_record_id
                ):
                    incomplete_reason = "final_axis_generation_changed"
                    break
                pages.append(page)
                next_cursor = page.get("next_cursor")
                if next_cursor is None or next_cursor == cursor:
                    break
                cursor = str(next_cursor)
                if not page["rows"]:
                    break

            if incomplete_reason is None and (
                not pages or pages[-1].get("next_cursor") is not None
            ):
                incomplete_reason = "final_axis_not_drained"
            if incomplete_reason is None and (
                not bool(pages[-1].get("finalized"))
                or not bool(pages[-1].get("complete"))
            ):
                incomplete_reason = "final_axis_incomplete"

        verified: dict[str, Any] | None = None
        if incomplete_reason is None:
            verify_raw = client.get_battle_record(
                battle_record_id=source_record_id,
                subtract_time_stop=True,
            )
            if verify_raw is None:
                incomplete_reason = "final_record_disappeared"
            else:
                verified = parse_battle_record(verify_raw)
                self._require_contract_v5(verified)
                if (
                    str(verified["generation"]) != generation
                    or str(verified["battle_record_id"]) != source_record_id
                    or str(verified.get("state") or "") != "finalized"
                ):
                    incomplete_reason = "final_axis_generation_changed"

        writer = self._summary_writer
        if incomplete_reason is None:
            if writer is not None:
                writer.replace_axis_pages(
                    capture_operation_id=self._operation_context.operation_id,
                    pages=pages,
                    source_generation=generation,
                )
            return verified

        if writer is not None:
            writer.replace_axis_pages(
                capture_operation_id=self._operation_context.operation_id,
                pages=(),
                source_generation=generation,
                incomplete_reason=incomplete_reason,
            )
        incomplete_record = dict(record)
        incomplete_record["axis_complete"] = False
        incomplete_record["finalization_incomplete_reason"] = incomplete_reason
        return incomplete_record

    @staticmethod
    def _require_contract_v5(payload: Mapping[str, Any]) -> None:
        if int(payload.get("contract_version") or 0) < _BATTLE_READ_CONTRACT_VERSION:
            raise RuntimeError(
                "当前 nte-core 战斗契约低于 v5，不能开始新的战报采集"
            )

    def _on_capture_status(self, event: dict[str, object]) -> None:
        # This client owns one combat capture. The reader callback only wakes
        # the owner; stop RPC and final page reads must run outside this callback.
        params = event.get("params")
        if self._required_source == "packet":
            if (isinstance(params, Mapping) and params.get("profile") == "combat"
                    and params.get("operation_id") and params.get("status") in {"failed", "stopped"}
                    and not self._stop_event.is_set()):
                self._event_error = RuntimeError("Core 已报告抓包采集意外停止，请查看检测详情。")
                self._stop_event.set()
            return
        if (isinstance(params, Mapping) and params.get("profile") == "combat"
                and params.get("status") == "stopped"
                and params.get("transport_complete") is True
                and params.get("operation_id")):
            self._native_terminal = dict(params)
            self._publish(
                "stopping", "正在结束当前场景采集并读取最终战报。",
                running=True, summary=self._latest_summary,
                end_reason=native_capture_end_reason(self._native_terminal),
            )
            self._stop_event.set()

    def _on_summary_event(self, event: dict[str, object]) -> None:
        if self._packet_capture_waiting or self._discard_was_requested():
            return
        try:
            summary = parse_battle_summary_event(event)
        except Exception as error:
            self._event_error = error
            self._summary_event.set()
            self._stop_event.set()
            return
        with self._lock:
            if summary.sequence and summary.sequence <= self._last_sequence:
                return
            self._last_sequence = max(self._last_sequence, summary.sequence)
            self._latest_summary = summary
        self._summary_event.set()
        current_phase = self.state.phase
        self._publish(
            "stopping" if current_phase == "stopping" else "running",
            (
                "正在停止采集并读取最终战报……"
                if current_phase == "stopping"
                else ("采集中：已收到实时战斗数据。" if has_active_battle_observation(summary)
                      else "采集已就绪，等待战斗数据。")
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
        end_reason: str | None = None,
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
            end_reason=end_reason or self._end_reason(),
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
