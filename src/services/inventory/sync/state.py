# 定义背包同步状态与统一UTC回执时间，保留未知事实与停止原因。
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


SyncPhase = Literal[
    "stopped",
    "starting",
    "waiting",
    "collecting",
    "saving",
    "listening",
    "error",
]


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
    source_snapshot_ready: bool = False
    capture_source: Literal["packet", "native"] = "packet"
    error: str | None = None
    error_code: str | None = None
    stop_reason: str | None = None
    character_sync_revision: int = 0
    character_sync_error: str | None = None
    updated_at_utc: str = ""


def sync_timestamp_utc() -> str:
    # 同步状态与保存回执使用同一UTC毫秒格式，不改变事实时间口径。
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")
