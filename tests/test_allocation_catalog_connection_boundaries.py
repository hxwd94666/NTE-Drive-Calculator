# 固化配装目录重建期间账号数据库连接的复用。
"""Guard the account connection count during one allocation-catalog rebuild.

逐角色各开一次账号库会把建连与迁移检查按角色数放大（实测一次重建 27 次打开）。
目录现在把已打开的账号连接交给角色详情复用，这条测试用于防止回归。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services import legacy_allocation_static_catalog as catalog_module
from src.services import official_role_page_service as page_module
from src.storage.sqlite.user_data_dao import UserDataDao

ROOT = Path(__file__).resolve().parents[1]
MAX_ACCOUNT_OPENS = 3


class AllocationCatalogConnectionTests(unittest.TestCase):
    def test_rebuild_reuses_one_account_connection(self) -> None:
        """一次目录重建只允许少量账号库连接，不得按角色逐个打开。"""

        static_database = ROOT / "data" / "game_static.sqlite3"
        if not static_database.is_file():
            self.skipTest("缺少静态库数据")
        temporary = tempfile.mkdtemp(prefix="nte-catalog-conn-")
        user_database = Path(temporary) / "user.sqlite3"
        UserDataDao(user_database, account_id="conn", account_name="conn").close()

        opened: list[bool] = []

        class CountingUserDataDao(UserDataDao):
            def __init__(self, *args: object, **kwargs: object) -> None:
                opened.append(True)
                super().__init__(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch.object(catalog_module, "UserDataDao", CountingUserDataDao),
            patch.object(page_module, "UserDataDao", CountingUserDataDao),
        ):
            catalog_module.build_legacy_allocation_static_catalog(
                config_dir=ROOT / "config",
                user_database_path=user_database,
                static_database_path=static_database,
            )

        self.assertLessEqual(
            len(opened), MAX_ACCOUNT_OPENS,
            f"一次目录重建打开了 {len(opened)} 次账号库，应不超过 {MAX_ACCOUNT_OPENS} 次",
        )


if __name__ == "__main__":
    unittest.main()
