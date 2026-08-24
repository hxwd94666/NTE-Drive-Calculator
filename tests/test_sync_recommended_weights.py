# 测试静态角色权重同步在 Windows 文件占用时的事务性回退。

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from tools.game_data.sync_recommended_weights import (
    main,
    reuse_static_database_recommendations,
    update_static_database,
)


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


def test_workshop_row_replaces_preimplementation_character_fallback() -> None:
    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "game_static.sqlite3"
        shutil.copy2(DATABASE, database)

        with patch(
            "tools.game_data.sync_recommended_weights.populate_graduation_templates",
            return_value=23,
        ):
            update_static_database(
                database,
                [{
                    "itemId": "1072",
                    "name": "灵可",
                    "weightConfig": {"weights": [{
                        "name": "灵属性异能伤害增强%",
                        "value": 1.73,
                        "main_value": 1.61,
                    }]},
                }],
            )

        with sqlite3.connect(database) as connection:
            recommendation = connection.execute(
                """SELECT source_kind, source_name
                   FROM character_weight_recommendation
                   WHERE character_id = 1072"""
            ).fetchone()
            weight = connection.execute(
                """SELECT weight, main_weight
                   FROM character_weight_recommendation_property
                   WHERE character_id = 1072
                     AND property_id = 'DamageUpNatureBase'"""
            ).fetchone()

    assert recommendation == ("workshop_api", "灵可")
    assert weight == (1.73, 1.61)


def test_missing_api_key_reuses_the_prebuild_release_backup() -> None:
    summary = {
        "reused_count": 22,
        "default_count": 1,
        "property_count": 146,
        "graduation_count": 23,
        "install_mode": "replace",
    }
    with (
        patch.object(sys, "argv", [
            "sync_recommended_weights.py",
            "--database", "new.sqlite3",
            "--reuse-database-if-missing", "previous.sqlite3",
        ]),
        patch(
            "tools.game_data.sync_recommended_weights.resolve_api_key",
            return_value=("", ""),
        ),
        patch(
            "tools.game_data.sync_recommended_weights.reuse_static_database_recommendations",
            return_value=summary,
        ) as reuse,
    ):
        result = main()

    assert result == 0
    reuse.assert_called_once()


def test_missing_api_key_and_missing_backup_blocks_release() -> None:
    with (
        patch.object(sys, "argv", [
            "sync_recommended_weights.py",
            "--database", "new.sqlite3",
            "--reuse-database-if-missing", "missing.sqlite3",
        ]),
        patch(
            "tools.game_data.sync_recommended_weights.resolve_api_key",
            return_value=("", ""),
        ),
        patch(
            "tools.game_data.sync_recommended_weights.reuse_static_database_recommendations",
            side_effect=FileNotFoundError("backup missing"),
        ),
    ):
        result = main()

    assert result == 1


def test_all_default_backup_cannot_satisfy_release_fallback() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        database = root / "new.sqlite3"
        backup = root / "previous.sqlite3"
        shutil.copy2(DATABASE, database)
        shutil.copy2(DATABASE, backup)
        try:
            reuse_static_database_recommendations(database, backup)
        except RuntimeError as exc:
            assert "不含 workshop_api/workshop_cache" in str(exc)
        else:
            raise AssertionError("全 default 备份不应通过发布回退门禁")
