# 验证发行包公共额外形状默认库的首次运行初始化规则。
"""Tests for seeding packaged public shared data."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.app.shared_data_seed import seed_shared_database
from src.services.custom_character_service import create_custom_character, save_custom_character_board
from src.storage.sqlite.user_data_dao import UserDataDao


class SharedDataSeedTests(unittest.TestCase):
    def test_packaged_database_replaces_the_local_public_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "bundled.sqlite3"
            data_root = root / "user-data"
            bundled.write_bytes(b"packaged-public-shape-data")

            seeded = seed_shared_database(bundled, data_root)
            self.assertEqual(data_root / "data" / "app_shared.sqlite3", seeded)
            self.assertEqual(bundled.read_bytes(), seeded.read_bytes())

            seeded.write_bytes(b"existing-user-override")
            self.assertEqual(seeded, seed_shared_database(bundled, data_root))
            self.assertEqual(bundled.read_bytes(), seeded.read_bytes())

    def test_public_seed_does_not_touch_an_account_custom_character_chassis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = root / "bundled.sqlite3"
            bundled.write_bytes(b"packaged-public-shape-data")
            account_database = root / "accounts" / "player" / "user_data.sqlite3"
            with UserDataDao(account_database, account_id="player"):
                pass
            custom = create_custom_character(account_database, "账号自建角色")
            with UserDataDao(account_database) as user_dao:
                cells = user_dao.list_custom_characters()[0]["board_cells"]
            edited = [
                {
                    "row": int(cell["row_number"]),
                    "column": int(cell["column_number"]),
                    "is_enabled": bool(cell["is_enabled"]),
                    "is_locked": bool(cell["is_locked"]),
                }
                for cell in cells
            ]
            for cell in edited:
                if (cell["row"], cell["column"]) == (4, 5):
                    cell["is_enabled"] = False
                elif (cell["row"], cell["column"]) == (5, 1):
                    cell["is_enabled"] = True
            save_custom_character_board(
                account_database, int(custom["character_id"]), edited
            )

            seed_shared_database(bundled, root / "app-data")

            with UserDataDao(account_database) as user_dao:
                stored = user_dao.list_custom_characters()[0]["board_cells"]
            enabled = {
                (int(cell["row_number"]), int(cell["column_number"]))
                for cell in stored
                if cell["is_enabled"]
            }
            self.assertNotIn((4, 5), enabled)
            self.assertIn((5, 1), enabled)
