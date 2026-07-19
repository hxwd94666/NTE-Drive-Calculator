# 测试账号生命周期与分账号用户数据库的联动。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.features.accounts.manager import AccountManager
from src.storage.sqlite.user_data_dao import UserDataDao


class AccountUserDatabaseTests(unittest.TestCase):
    def make_manager(self, root: Path) -> AccountManager:
        bundled = root / "bundled_config"
        bundled.mkdir()
        return AccountManager(
            data_root=root / "data",
            bundled_config_dir=bundled,
            iter_image_files=lambda _path: [],
            core_config_files=(),
            account_user_files=(),
        )

    def test_account_lifecycle_creates_and_renames_user_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            manager = self.make_manager(Path(temporary))

            default_state = manager.initialize()
            self.assertTrue(default_state.user_database_path.is_file())
            with UserDataDao(default_state.user_database_path) as database:
                self.assertEqual(database.profile()["account_name"], "默认账号")

            account_id = manager.create_account("测试账号")
            database_path = manager.account_dir(account_id) / "user_data.sqlite3"
            self.assertTrue(database_path.is_file())

            manager.rename_account(account_id, "新名称")
            with UserDataDao(database_path) as database:
                self.assertEqual(database.profile()["account_name"], "新名称")


if __name__ == "__main__":
    unittest.main()
