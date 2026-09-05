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
from .inventory_sync_logging import (
    inventory_payload_log_fields,
    stored_snapshot_log_fields,
)
from .inventory_snapshot_stabilizer import InventorySnapshotStabilizer


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

def run_inventory_sync(service: Any) -> None:
    client: InventoryCoreClient | None = None
    fatal_error: Exception | None = None
    try:
        with service._open_dao() as dao:
            settings = AccountSettingsService(service.database_path).load("sync")
            settle_seconds = (
                service._settle_seconds
                if service._settle_seconds is not None
                else float(settings["inventory_settle_seconds"])
            )
            stabilizer = InventorySnapshotStabilizer(settle_seconds)
            current_id = dao.current_inventory_snapshot_id()
            current_has_character_instances = (
                dao.snapshot_has_independent_character_instances(current_id)
                if current_id is not None
                else False
            )
            if current_id is not None:
                previous = dao.raw_snapshot(current_id)
                if previous:
                    try:
                        stabilizer.seed_committed(previous)
                    except ValueError:
                        pass

            client = service._client_factory()
            service._client = client
            client.start()
            client.add_event_handler("event.inventory.snapshot", service._on_inventory_event)
            log_event(
                "INFO",
                "inventory_sync.core_connected",
                "背包同步已连接 nte-core",
                service._operation_context,
                protocol_version=service._protocol_version(client),
                capabilities=list((client.hello_result or {}).get("capabilities") or ()),
            )
            capture_device = service._capture_device_id
            if capture_device is None:
                capture_device = settings.get("capture_device_id")
            raw_enabled = service._raw_capture_enabled
            if raw_enabled is None:
                raw_enabled = bool(settings.get("raw_capture_enabled"))
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
            client.start_capture(
                profile="inventory",
                device_name=capture_device,
                raw_capture="enabled" if raw_enabled else "disabled",
            )
            log_event(
                "INFO",
                "inventory_sync.capture_started",
                "背包同步抓包已启动，等待完整背包快照",
                service._operation_context,
                raw_capture=bool(raw_enabled),
                capture_device_configured=bool(capture_device),
            )
            current_summary = dao.current_inventory_summary()
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
                "waiting" if current_summary is None else "listening",
                "等待进入游戏并接收完整背包"
                if current_summary is None
                else (
                    "当前为旧版背包快照，未包含独立角色实例；"
                    "正在等待新 nte-core 写入完整角色快照"
                    if not current_has_character_instances
                    else "背包已同步，正在后台监听变化"
                ),
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
            guard_generation, _guard_uids = service._full_inventory_guard()
            while not service._stop_requested.is_set():
                service._event_ready.wait(service._poll_seconds)
                event = service._take_latest_event()
                if event is not None:
                    for source_snapshot_id, items, observed_at, sequence in (
                        service._take_pending_runtime_state_deltas()
                    ):
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
                        log_event(
                            "DEBUG",
                            "inventory_sync.legacy_event_ignored",
                            "忽略紧随角色实例快照后的旧格式背包事件",
                            service._operation_context,
                        )
                        continue
                    result = stabilizer.offer(event, required_uids=required_uids)
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
                            running=True,
                            capturing=True,
                            pending_item_count=result.item_count,
                            added_count=result.added_count,
                            removed_count=result.removed_count,
                            error=None,
                        )
                    elif result.status == "reverted":
                        log_event(
                            "DEBUG",
                            "inventory_sync.candidate_reverted",
                            "收到未变更的背包快照，继续监听",
                            service._operation_context,
                        )
                        service._publish(
                            "listening",
                            (
                                "收到与旧版快照相同的背包内容；其中未包含独立角色实例，"
                                "极速装配仍不可用。请确认本次运行的是新 nte-core，"
                                "并等待其输出带 characters 的完整快照"
                                if not current_has_character_instances
                                else "背包变化已撤销，继续后台监听"
                            ),
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
                service._publish(
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
                        protocol_version=service._protocol_version(client),
                    )
                except Exception as exc:
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
                    continue
                stabilizer.mark_committed(stable.fingerprint)
                current_has_character_instances = dao.snapshot_has_independent_character_instances(snapshot_id)
                committed_summary = dao.inventory_snapshot_summary(snapshot_id) or {}
                committed_context = service._operation_context.with_values(
                    snapshot_id=snapshot_id,
                )
                log_event(
                    "INFO",
                    "inventory_sync.snapshot_committed",
                    "已保存稳定背包快照",
                    committed_context,
                    protocol_version=service._protocol_version(client),
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
                    retention = dao.prune_inventory_snapshots()
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
                    error_code=None,
                )
    except Exception as exc:
        fatal_error = exc
        log_event(
            "ERROR",
            "inventory_sync.failed",
            "背包同步服务异常停止",
            service._operation_context,
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
                client.stop_capture()
            except Exception:
                pass
            service._prune_raw_captures()
            try:
                client.close()
            except Exception:
                pass
        service._client = None
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


def prune_raw_captures(service: Any) -> None:
    """Best-effort cleanup; packet capture must never fail because pruning did."""
    if service._raw_capture_directory is None:
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
