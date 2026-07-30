# 从固定账号快照加载仓库原始装备和已装备角色显示信息。
"""Application service for one immutable warehouse inventory snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from src.observability import OperationContext, operation_scope
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class WarehouseInventoryService:
    def __init__(
        self,
        database_path: str | Path,
        *,
        dao_factory: Callable[..., Any] = UserDataDao,
        static_dao_factory: Callable[..., Any] = StaticGameDataDao,
        operation_context: OperationContext | None = None,
    ) -> None:
        self.database_path = Path(database_path)
        self.dao_factory = dao_factory
        self.static_dao_factory = static_dao_factory
        self.operation_context = operation_context or OperationContext.create(
            "warehouse"
        )

    def load_current_snapshot(self) -> dict[str, Any]:
        with operation_scope(
            self.operation_context,
            started_event="warehouse.load_started",
            succeeded_event="warehouse.load_succeeded",
            failed_event="warehouse.load_failed",
            message="读取仓库稳定快照",
        ) as span:
            result = self._load_current_snapshot()
            summary = result.get("summary")
            summary_fields = (
                {
                    "module_count": int(summary.get("module_count") or 0),
                    "core_count": int(summary.get("core_count") or 0),
                    "equipped_count": int(summary.get("equipped_count") or 0),
                    "locked_count": int(summary.get("locked_count") or 0),
                    "character_instance_count": int(
                        summary.get("character_instance_count") or 0
                    ),
                    "generation": summary.get("generation"),
                    "sequence": summary.get("sequence"),
                }
                if isinstance(summary, Mapping)
                else {}
            )
            span.annotate(
                snapshot_id=result.get("snapshot_id"),
                source=result.get("source"),
                item_count=len(result.get("rows") or ()),
                **summary_fields,
            )
            return result

    def _load_current_snapshot(self) -> dict[str, Any]:
        if not self.database_path.is_file():
            return {"snapshot_id": None, "source": "", "rows": []}
        with self.dao_factory(self.database_path) as dao, self.static_dao_factory() as static_dao:
            snapshot_id = dao.current_inventory_snapshot_id()
            if snapshot_id is None:
                return {"snapshot_id": None, "source": "", "rows": []}
            summary = dao.inventory_snapshot_summary(snapshot_id) or {}
            source = str(summary.get("source") or "")
            rows = dao.list_inventory_items(snapshot_id)
            character_names = {
                int(character["character_id"]): str(
                    character.get("name_zh") or ""
                )
                for character in static_dao.list_characters()
                if character.get("character_id") is not None
            }
            character_ids_by_instance = {
                (
                    int(mapping["uid_slot"]),
                    int(mapping["uid_serial"]),
                ): int(mapping["character_id"])
                for mapping in dao.list_character_instance_mappings()
            }
        for row in rows:
            self._add_equipped_character_name(
                row,
                character_ids_by_instance,
                character_names,
            )
        return {
            "snapshot_id": snapshot_id,
            "source": source,
            "rows": rows,
            "summary": summary,
        }

    @staticmethod
    def _add_equipped_character_name(
        row: dict[str, Any],
        character_ids_by_instance: Mapping[tuple[int, int], int],
        character_names: Mapping[int, str],
    ) -> None:
        character_id = row.get("equipped_character_id")
        if character_id is None and isinstance(
            row.get("equipped_character_uid"),
            Mapping,
        ):
            character_uid = row["equipped_character_uid"]
            try:
                character_id = character_ids_by_instance.get(
                    (
                        int(character_uid["slot"]),
                        int(character_uid["serial"]),
                    )
                )
            except (KeyError, TypeError, ValueError):
                character_id = None
            if character_id is not None:
                # Display-only recovery from historical snapshot evidence.
                row["equipped_character_id"] = character_id
        if isinstance(character_id, int):
            row["equipped_character_name"] = character_names.get(
                character_id,
                "",
            )
