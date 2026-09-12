# 承担 InventorySyncService 的后台捕获、稳定化与持久化运行循环。
"""Runtime loop extracted from the public inventory sync service."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from src.observability import log_event
from src.services.account_settings_service import AccountSettingsService
from src.services.raw_capture_retention import prune_raw_capture_files
from src.storage.sqlite.inventory_save_error import InventorySnapshotSaveError
from src.utils.logger import logger

from .inventory_sync_contracts import InventoryCoreClient
from .inventory_capture_wait import CaptureStartError, InventorySyncCancelled, require_inventory_operation, wait_capture_ready
from .inventory_sync_logging import (
    InventorySyncDiagnostics,
    inventory_core_log_fields,
    inventory_payload_log_fields,
    stored_snapshot_log_fields,
)
from .inventory_snapshot_stabilizer import InventorySnapshotStabilizer, SnapshotOfferResult
from .inventory_source_capabilities import has_native_inventory_uids, is_visual_inventory_source


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _snapshot_listening_message(
    summary: Mapping[str, Any] | None,
    has_character_list: bool,
    *,
    received: bool = False,
) -> str:
    if summary is None:
        return "等待进入游戏并接收完整背包"
    source = summary.get("source")
    if is_visual_inventory_source(source):
        return "当前为视觉扫描库存，正在等待原生背包同步；视觉扫描不提供角色实例"
    if not has_native_inventory_uids(source):
        return "当前库存来源尚不支持原生角色身份，正在等待原生背包同步"
    prefix = "已收到原生背包" if received else "当前已保存原生背包"
    if not has_character_list:
        return f"{prefix}，该快照未附带独立角色列表；正在后台监听更新"
    if not summary.get("character_instance_count"):
        return f"{prefix}，尚未观测到角色实例；正在后台监听更新"
    return "背包已同步，正在后台监听变化"


def _snapshot_waiting_message(summary, has_character_list):
    if summary is None:
        return "监听已就绪，等待进入游戏并接收背包数据。"
    source = summary.get("source")
    if is_visual_inventory_source(source):
        previous = "当前为视觉扫描库存，不提供角色实例"
    elif has_native_inventory_uids(source) and not has_character_list:
        previous = "上次原生背包未附带独立角色列表"
    elif not has_native_inventory_uids(source):
        previous = "上次库存来源尚不支持原生角色身份"
    else:
        previous = "上次保存的背包仍可用于计算"
    return f"监听已就绪，等待本次背包数据；{previous}。"


def run_inventory_sync(service: Any) -> None:
    client: InventoryCoreClient | None = None
    fatal_error: Exception | None = None
    diagnostics = InventorySyncDiagnostics(service._operation_context)
    sync_stage = "loading_settings"
    stabilizer: InventorySnapshotStabilizer | None = None
    current_id: int | None = None
    try:
        require_inventory_operation(service)
        with service._open_dao() as dao:
            settings = AccountSettingsService(service.database_path).load("sync")
            native = service.capture_source == "native"
            settle_seconds = (
                service._settle_seconds
                if service._settle_seconds is not None
                else (0.2 if native else float(settings["inventory_settle_seconds"]))
            )
            stabilizer = InventorySnapshotStabilizer(settle_seconds)
            current_id = dao.current_inventory_snapshot_id()
            current_summary = (
                dao.inventory_snapshot_summary(current_id) if current_id is not None else None
            )
            current_has_character_instances = (
                dao.snapshot_has_independent_character_instances(current_id)
                if current_id is not None
                else False
            )
            # 视觉库存不是原生同步的去重基线；即使内容相同，也必须保存原生来源。
            if (
                current_id is not None
                and current_summary is not None
                and has_native_inventory_uids(current_summary.get("source"))
            ):
                previous = dao.raw_snapshot(current_id)
                if previous:
                    try:
                        stabilizer.seed_committed(previous)
                    except ValueError:
                        log_event(
                            "WARNING", "inventory_sync.baseline_invalid",
                            "已保存快照无法作为去重基线，将等待新的完整快照",
                            service._operation_context.with_values(snapshot_id=current_id),
                        )

            log_event(
                "INFO", "inventory_sync.baseline_selected", "已确定本次同步去重基线",
                service._operation_context.with_values(snapshot_id=current_id),
                baseline_seeded=stabilizer.committed_fingerprint is not None,
                settle_seconds=settle_seconds,
                source=current_summary.get("source") if current_summary else None,
                item_count=current_summary.get("stored_item_count") if current_summary else None,
                character_instances_independent=current_has_character_instances,
            )

            sync_stage = "starting_core"
            require_inventory_operation(service)
            client = service._client_factory()
            service._client = client
            client.start()
            client.add_event_handler("event.inventory.snapshot", service._on_inventory_event)
            client.add_event_handler("event.capture.status", service._on_capture_status_event)
            log_event(
                "INFO",
                "inventory_sync.core_connected",
                "背包同步已连接 nte-core",
                service._operation_context,
                protocol_version=service._protocol_version(client),
                capabilities=list((client.hello_result or {}).get("capabilities") or ()),
                **inventory_core_log_fields(client.hello_result or {}),
            )
            capture_device = service._capture_device_id
            if capture_device is None:
                capture_device = settings.get("capture_device_id")
            raw_enabled = service._raw_capture_enabled
            if raw_enabled is None:
                raw_enabled = bool(settings.get("raw_capture_enabled"))
            if native:
                capture_device, raw_enabled = None, False
            if raw_enabled:
                require_inventory_operation(service, "diagnostics")
            if raw_enabled and service._raw_capture_directory is not None:
                service._raw_capture_directory.mkdir(parents=True, exist_ok=True)
                service._prune_raw_captures()
                log_event(
                    "DEBUG",
                    "inventory_sync.raw_capture_enabled",
                    "已启用 nte-core 原始抓包诊断",
                    service._operation_context,
                    directory=service._raw_capture_directory,
                )
            sync_stage = "starting_capture"
            require_inventory_operation(service)
            supports_wait = not native and "capture_wait_v1" in (client.hello_result or {}).get("capabilities", ())
            capture_result = client.start_capture(
                profile="inventory",
                device_name=capture_device,
                raw_capture="enabled" if raw_enabled else "disabled",
                **({"wait_for_game": True} if supports_wait else {}),
            )
            service._capture_monitor.update(capture_result)
            sync_stage = "waiting_capture_ready"
            service._publish(
                "starting",
                "正在连接 DLL 背包来源" if native else "正在初始化抓包，等待网卡就绪",
                running=True,
                capturing=False,
                last_snapshot_id=current_id,
            )
            if not wait_capture_ready(service, client, supports_wait=supports_wait, diagnostics=bool(raw_enabled)):
                return
            log_event(
                "INFO",
                "inventory_sync.capture_started",
                "背包同步来源已就绪，等待完整背包快照",
                service._operation_context,
                raw_capture=bool(raw_enabled),
                capture_device_configured=bool(capture_device),
            )
            sync_stage = "listening"
            if current_summary is not None and current_id is not None:
                log_event(
                    "INFO",
                    "inventory_sync.current_snapshot_loaded",
                    "已加载当前稳定背包摘要",
                    service._operation_context.with_values(snapshot_id=current_id),
                    **stored_snapshot_log_fields(
                        current_summary,
                        character_instances_independent=current_has_character_instances,
                    ),
                )
            service._publish(
                "waiting",
                _snapshot_waiting_message(current_summary, current_has_character_instances),
                running=True,
                capturing=not native,
                last_snapshot_id=current_id,
                last_item_count=(
                    int(current_summary["stored_item_count"])
                    if current_summary is not None
                    else None
                ),
            )

            retry_save_at = 0.0
            next_native_status = 0.0
            native_status_message = None
            guard_generation, _guard_uids = service._full_inventory_guard()
            while not service._stop_requested.is_set():
                service._event_ready.wait(service._poll_seconds)
                require_inventory_operation(service)
                if raw_enabled:
                    require_inventory_operation(service, "diagnostics")
                if native and time.monotonic() >= next_native_status:
                    native_status = client.status()
                    require_inventory_operation(service)
                    service._capture_monitor.update(native_status)
                    next_native_status = time.monotonic() + (
                        0.2 if native_status.get("native_change_pending") else 1.0
                    )
                    _apply_native_profiles(service, native_status)
                    if not native_status.get("native_snapshot_ready", False):
                        stabilizer.discard_pending()
                        service._take_latest_event()  # Do not re-offer an observation invalidated during this poll.
                        message = str(native_status.get("message") or "等待 DLL 提供完整背包快照。")
                        if message != native_status_message:
                            service._publish("waiting", message, running=True, capturing=False, source_snapshot_ready=False)
                        native_status_message = message
                    else:
                        native_status_message = None
                capture_status, capture_error = service._capture_monitor.read()
                if capture_status == "failed":
                    raise CaptureStartError(capture_error or "CAPTURE_FAILED")
                diagnostics.summary(
                    phase=service.state.phase, pending_item_count=stabilizer.pending_item_count,
                    snapshot_id=current_id,
                )
                event = service._take_latest_event()
                if event is not None:
                    sync_stage = "processing_event"
                    for source_snapshot_id, items, observed_at, sequence in (
                        service._take_pending_runtime_state_deltas()
                    ):
                        require_inventory_operation(service)
                        updated_count = dao.apply_inventory_runtime_state_delta(
                            source_snapshot_id,
                            items,
                            observed_at_unix_ms=observed_at,
                            sequence=sequence,
                        )
                        if updated_count:
                            log_event(
                                "DEBUG",
                                "inventory_sync.runtime_state_delta_applied",
                                "已合并局部装备状态，不变更完整背包快照",
                                service._operation_context,
                                updated_count=updated_count,
                            )
                    current_guard_generation, required_uids = service._full_inventory_guard()
                    if current_guard_generation != guard_generation:
                        # A candidate collected before the apply guard must
                        # not settle after the guard is installed (or removed).
                        stabilizer.discard_pending()
                        guard_generation = current_guard_generation
                    # Some transitions from the old capture stream emit a
                    # legacy inventory event immediately after the new
                    # v0.3.5 event.  If the saved snapshot is legacy and a
                    # candidate already has independent character UIDs,
                    # allowing that fallback through would erase the
                    # candidate as a mere "revert" before it can settle.
                    # Only prefer the richer event during this one-time
                    # format upgrade; normal inventory changes remain
                    # governed by the stabilizer.
                    if (
                        not current_has_character_instances
                        and not service._event_has_independent_character_instances(event)
                        and stabilizer.pending_has_independent_character_instances
                    ):
                        diagnostics.record(
                            event,
                            SnapshotOfferResult("ignored", reason_code="legacy_candidate_preferred"),
                            guard_item_count=len(required_uids) if required_uids is not None else None,
                        )
                        log_event(
                            "DEBUG",
                            "inventory_sync.legacy_event_ignored",
                            "忽略紧随角色实例快照后的旧格式背包事件",
                            service._operation_context,
                        )
                        sync_stage = "listening"
                        continue
                    result = stabilizer.offer(event, required_uids=required_uids)
                    diagnostics.record(
                        event, result,
                        guard_item_count=len(required_uids) if required_uids is not None else None,
                    )
                    if result.status in {"collecting", "changed"}:
                        candidate_fields = inventory_payload_log_fields(event)
                        log_event(
                            "DEBUG",
                            "inventory_sync.candidate_received",
                            "已接收背包快照候选，等待内容稳定",
                            service._operation_context,
                            added_count=result.added_count,
                            removed_count=result.removed_count,
                            candidate_status=result.status,
                            **candidate_fields,
                        )
                        service._publish(
                            "collecting",
                            f"已接收 {result.item_count} 件，等待背包内容稳定",
                            source_snapshot_ready=True,
                            running=True,
                            capturing=True,
                            pending_item_count=result.item_count,
                            added_count=result.added_count,
                            removed_count=result.removed_count,
                            error=None,
                        )
                    elif result.status in {"reverted", "unchanged"}:
                        log_event(
                            "DEBUG",
                            "inventory_sync.candidate_reverted"
                            if result.status == "reverted"
                            else "inventory_sync.snapshot_unchanged",
                            "收到未变更的背包快照，继续监听",
                            service._operation_context,
                        )
                        service._publish(
                            "listening",
                            _snapshot_listening_message(
                                current_summary, current_has_character_instances, received=True,
                            ),
                            source_snapshot_ready=True,
                            running=True,
                            capturing=True,
                            pending_item_count=None,
                            added_count=0,
                            removed_count=0,
                        )

                sync_stage = "listening"
                now = time.monotonic()
                stable = stabilizer.ready(now=now)
                if stable is None or now < retry_save_at:
                    continue
                if native and not client.confirm_inventory_snapshot(stable.payload.get("native_snapshot")):
                    # Validate only the candidate's revision. Do not start a full
                    # read here or let an unrelated character refresh delay saving.
                    stabilizer.discard_pending()
                    next_native_status = 0.0
                    service._publish("waiting", "背包又有变化，正在合并更新。", running=True,
                                     capturing=False, source_snapshot_ready=False, pending_item_count=None)
                    continue
                service._publish(
                    "saving",
                    f"背包已稳定，正在保存 {stable.item_count} 件",
                    running=True,
                    capturing=True,
                    pending_item_count=stable.item_count,
                )
                sync_stage = "saving_snapshot"
                require_inventory_operation(service)
                try:
                    snapshot_id = dao.import_inventory_snapshot(
                        stable.message,
                        source="nte_core",
                        protocol_version=service._protocol_version(client),
                    )
                except Exception as exc:
                    diagnostics.save_failure_count += 1
                    save_diagnostics = (
                        exc.diagnostics if isinstance(exc, InventorySnapshotSaveError) else {}
                    )
                    log_event(
                        "WARNING",
                        "inventory_sync.snapshot_commit_retry",
                        "保存稳定背包失败，将自动重试",
                        service._operation_context,
                        error=exc,
                        retry_delay_seconds=2,
                        save_attempt_count=diagnostics.save_failure_count + diagnostics.committed_count,
                        candidate_item_count=stable.item_count,
                        **save_diagnostics,
                    )
                    retry_save_at = time.monotonic() + 2.0
                    service._publish(
                        "error",
                        "保存稳定背包失败，后台将自动重试",
                        running=True,
                        capturing=True,
                        error=f"{type(exc).__name__}: {exc}",
                        error_code=(
                            exc.error_code
                            if isinstance(exc, InventorySnapshotSaveError)
                            else "SNAPSHOT_SAVE_FAILED"
                        ),
                    )
                    sync_stage = "waiting_save_retry"
                    continue
                stabilizer.mark_committed(stable.fingerprint)
                diagnostics.committed_count += 1
                sync_stage = "finalizing_snapshot"
                previous_item_count = current_summary.get("stored_item_count") if current_summary else None
                previous_source = current_summary.get("source") if current_summary else None
                current_id = snapshot_id
                current_has_character_instances = dao.snapshot_has_independent_character_instances(snapshot_id)
                committed_summary = dao.inventory_snapshot_summary(snapshot_id) or {}
                current_summary = committed_summary
                committed_context = service._operation_context.with_values(
                    snapshot_id=snapshot_id,
                )
                log_event(
                    "INFO",
                    "inventory_sync.snapshot_committed",
                    "已保存稳定背包快照",
                    committed_context,
                    protocol_version=service._protocol_version(client),
                    previous_item_count=previous_item_count,
                    previous_source=previous_source,
                    stable_seconds=round(now - stable.last_changed_at, 3),
                    **stored_snapshot_log_fields(
                        committed_summary,
                        character_instances_independent=current_has_character_instances,
                    ),
                )
                if service._template_refresh is not None:
                    try:
                        refreshed = service._template_refresh()
                        if isinstance(refreshed, Mapping) and refreshed.get("changed"):
                            log_event(
                                "INFO",
                                "inventory_sync.templates_refreshed",
                                "已刷新公共角色与弧盘模板",
                                committed_context,
                                role_count=int(refreshed.get("role_count", 0)),
                                fork_count=int(refreshed.get("fork_count", 0)),
                            )
                    except Exception as exc:
                        # 背包快照已经成功提交，模板缓存刷新不能阻断同步监听。
                        log_event(
                            "WARNING",
                            "inventory_sync.template_refresh_failed",
                            "公共角色与弧盘模板刷新失败，将在下次同步重试",
                            committed_context,
                            error=exc,
                        )
                try:
                    retention = dao.prune_inventory_snapshots(retain_recent=0 if native else None)
                    if retention["deleted_snapshot_count"]:
                        log_event(
                            "INFO",
                            "inventory_sync.retention_applied",
                            "已按保留策略清理历史背包快照",
                            committed_context,
                            deleted_snapshot_count=retention["deleted_snapshot_count"],
                            retained_snapshot_count=retention["total_after"],
                        )
                except Exception as exc:
                    # 新快照已经安全提交，清理失败不能让同步服务重新导入同一份数据。
                    log_event(
                        "WARNING",
                        "inventory_sync.retention_failed",
                        "历史背包快照清理失败，将在下次同步或手动维护时重试",
                        committed_context,
                        error=exc,
                    )
                retry_save_at = 0.0
                service._publish(
                    "listening",
                    _snapshot_listening_message(
                        current_summary, current_has_character_instances, received=True,
                    ),
                    running=True,
                    capturing=True,
                    pending_item_count=None,
                    added_count=0,
                    removed_count=0,
                    last_snapshot_id=snapshot_id,
                    last_item_count=stable.item_count,
                    last_synced_at_utc=_utc_now(),
                    error=None,
                    error_code=None,
                )
                sync_stage = "listening"
    except InventorySyncCancelled:
        pass
    except Exception as exc:
        fatal_error = exc
        log_event(
            "ERROR",
            "inventory_sync.failed",
            "背包同步服务异常停止",
            service._operation_context,
            failure_stage=sync_stage,
            error=exc,
            error_code=(
                str(getattr(exc, "domain_code"))
                if getattr(exc, "domain_code", None)
                else type(exc).__name__
            ),
        )
        service._publish(
            "error",
            "背包同步服务已停止",
            running=False,
            capturing=False,
            error=f"{type(exc).__name__}: {exc}",
            error_code=(
                str(getattr(exc, "domain_code"))
                if getattr(exc, "domain_code", None)
                else type(exc).__name__
            ),
        )
    finally:
        if client is not None:
            try:
                client.remove_event_handler("event.inventory.snapshot", service._on_inventory_event)
            except Exception:
                pass
            try:
                client.remove_event_handler("event.capture.status", service._on_capture_status_event)
            except Exception:
                pass
            try:
                client.stop_capture()
            except Exception:
                pass
            service._prune_raw_captures()
            try:
                client.close()
            except Exception:
                pass
        service._client = None
        diagnostics.summary(
            phase="failed" if fatal_error is not None else "stopped",
            pending_item_count=stabilizer.pending_item_count if stabilizer is not None else None,
            snapshot_id=current_id, final=True,
        )
        if fatal_error is None:
            log_event(
                "INFO",
                "inventory_sync.stopped",
                "背包同步已停止",
                service._operation_context,
            )
            service._publish(
                "stopped",
                "背包同步已停止",
                running=False,
                capturing=False,
                pending_item_count=None,
            )


def _apply_native_profiles(service, status):
    apply = service._native_profiles_apply
    if apply is None:
        return
    snapshot = status.get("native_character_snapshot")
    error = status.get("native_character_error")
    if snapshot is not None:
        try:
            require_inventory_operation(service)
            apply(snapshot["profiles"], check=lambda: require_inventory_operation(service))
            require_inventory_operation(service)
        except (InventorySyncCancelled, PermissionError):
            raise
        except Exception as exc:
            log_event("WARNING", "inventory_sync.character_save_failed", "角色自动同步未保存",
                      service._operation_context, error_type=type(exc).__name__)
            error = "角色自动同步未保存，已保留原养成；稍后自动重试。"
        else:
            service._publish(service.state.phase, service.state.message,
                             character_sync_revision=service.state.character_sync_revision + 1,
                             character_sync_error=None)
    if error and error != service.state.character_sync_error:
        service._publish(service.state.phase, service.state.message, character_sync_error=error)


def prune_raw_captures(service: Any) -> None:
    """Best-effort cleanup; packet capture must never fail because pruning did."""
    if service.capture_source == "native" or service._raw_capture_directory is None:
        return
    try:
        result = prune_raw_capture_files(service._raw_capture_directory)
    except Exception as exc:
        logger.warning(f"清理 nte-core .pcapng 诊断文件失败：{exc}")
        return
    if result.deleted_count:
        logger.info(
            "已自动清理 {} 个旧 .pcapng 诊断文件，释放 {:.1f} MiB；"
            "当前保留 {} 个",
            result.deleted_count,
            result.deleted_bytes / (1024 * 1024),
            result.retained_count,
        )
