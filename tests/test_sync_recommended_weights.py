# 测试静态角色权重同步在 Windows 文件占用时的事务性回退。

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.game_data.sync_recommended_weights import update_static_database


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "game_static.sqlite3"


def test_locked_windows_database_uses_online_backup_and_refreshes_manifest() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "game_static.sqlite3"
        manifest = root / "manifest.json"
        shutil.copy2(DATABASE, database)
        sharing_error = PermissionError(13, "sharing violation", str(database))
        sharing_error.winerror = 5

        with (
            patch(
                "tools.game_data.sync_recommended_weights.os.replace",
                side_effect=sharing_error,
            ),
            patch(
                "tools.game_data.sync_recommended_weights.populate_graduation_templates",
                return_value=22,
            ),
        ):
            summary = update_static_database(
                database,
                [{
                    "itemId": "1036",
                    "name": "残虹",
                    "weightConfig": {"weights": [
                        {"name": "暴击率%", "value": 1.25, "main_value": 1.1},
                    ]},
                }],
                manifest_path=manifest,
            )

        with sqlite3.connect(database) as connection:
            source_kind = connection.execute(
                """SELECT source_kind
                   FROM character_weight_recommendation
                   WHERE character_id = 1036"""
            ).fetchone()[0]
            weight = connection.execute(
                """SELECT weight
                   FROM character_weight_recommendation_property
                   WHERE character_id = 1036 AND property_id = 'CritBase'"""
            ).fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        digest = hashlib.sha256(database.read_bytes()).hexdigest().upper()

    assert summary["install_mode"] == "online_backup"
    assert source_kind == "workshop_api"
    assert weight == 1.25
    assert integrity == "ok"
    assert payload["database"]["sha256"] == digest
    assert not any(root.glob(".game_static.sqlite3.*.tmp"))
