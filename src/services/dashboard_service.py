# 聚合首页所需的静态数据、账号数据和最近背包变化。
"""聚合首页所需的静态数据、账号数据和最近背包变化。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class DashboardService:
    """只读首页查询边界，避免界面直接拼接多套 DAO 查询。"""

    def __init__(
        self,
        user_database_path: str | Path,
        *,
        static_database_path: str | Path | None = None,
    ) -> None:
        self.user_database_path = Path(user_database_path)
        self.static_database_path = static_database_path

    def load(self) -> dict[str, Any]:
        with UserDataDao(self.user_database_path) as user_dao:
            user_summary = user_dao.summary()
            inventory = user_summary["inventory"]
            snapshot_id = inventory["snapshot_id"] if inventory else None
            synced_ids = {
                row["character_id"] for row in user_dao.list_character_instance_mappings()
                if snapshot_id is not None and row["source"] == "snapshot"
                and row["last_seen_snapshot_id"] == snapshot_id
            }
            observed_profiles = user_dao.list_native_character_profile_observations()
            snapshots = user_dao.list_inventory_snapshots()
            recent_change = None
            if len(snapshots) >= 2:
                recent_change = user_dao.inventory_snapshot_diff(
                    snapshots[1]["snapshot_id"],
                    snapshots[0]["snapshot_id"],
                )

        with StaticGameDataDao(self.static_database_path) as static_dao:
            static_summary = static_dao.summary()
            roles = static_dao.list_role_template_characters()
            logical_keys = {row["character_id"]: row["logical_character_key"] or f"character:{row['character_id']}"
                            for row in static_dao.list_characters()}
            catalog_keys = {logical_keys[row["character_id"]] for row in roles}
            synced_keys = {logical_keys.get(cid) for cid in synced_ids} & catalog_keys
            profile_keys = {logical_keys.get(row["character_id"]) for row in observed_profiles
                            if row["character_level"] is not None and row["breakthrough_stage"] is not None} & catalog_keys

        return {
            "account": user_summary["profile"],
            "sync_settings": user_summary["sync_settings"],
            "inventory": user_summary["inventory"],
            "snapshot_count": int(user_summary["snapshot_count"]),
            "loadout_plan_count": int(user_summary["loadout_plan_count"]),
            "static": static_summary,
            "characters": {"catalog_count": len(catalog_keys), "synced_count": len(synced_keys),
                           "profile_count": len(profile_keys)},
            "recent_change": recent_change,
        }
