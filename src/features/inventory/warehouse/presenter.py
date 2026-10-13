# 将仓库应用服务的原始快照转换为虚拟化卡片视图模型。
"""Warehouse snapshot presentation adapter."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.features.inventory.warehouse.cards import warehouse_item_view
from src.observability import OperationContext
from src.services.inventory.warehouse_inventory_service import WarehouseInventoryService


def load_warehouse_snapshot(
    database_path: str | Path,
    operation_context: OperationContext | None = None,
    *,
    static_database_path: str | Path | None = None,
    asset_root: str | Path | None = None,
) -> dict[str, Any]:
    snapshot = WarehouseInventoryService(
        database_path,
        operation_context=operation_context,
        static_database_path=static_database_path,
    ).load_current_snapshot()
    source = str(snapshot.get("source") or "")
    return {
        "snapshot_id": snapshot.get("snapshot_id"),
        "source": source,
        "items": [
            warehouse_item_view(row, source=source, asset_root=asset_root,
                                static_database_path=static_database_path)
            for row in snapshot.get("rows", [])
        ],
    }
