# 验证账号管理与战报导出共享账号昵称事实。

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.services.account_naming_service import AccountNamingService
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError


class AccountNamingServiceTests(unittest.TestCase):
    def test_current_name_accepts_legacy_database_profile_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "accounts.json"
            index.write_text(json.dumps({
                "active_account_id": "account-a",
                "accounts": [{"id": "account-a", "name": "管理页名称"}],
            }, ensure_ascii=False), encoding="utf-8")
            database = root / "user.sqlite3"
            with UserDataDao(
                database,
                account_id="account-a",
                account_name="旧数据库名称",
            ):
                pass

            service = AccountNamingService(
                accounts_index_path=index,
                user_database_path=database,
                account_id="account-a",
                context_is_current=lambda: True,
            )

            self.assertEqual("管理页名称", service.current_name())

    def test_rename_updates_index_and_database_profile_without_export_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = root / "accounts.json"
            index.write_text(json.dumps({
                "active_account_id": "account-a",
                "accounts": [{"id": "account-a", "name": "旧名称"}],
            }, ensure_ascii=False), encoding="utf-8")
            database = root / "user.sqlite3"
            with UserDataDao(
                database,
                account_id="account-a",
                account_name="旧名称",
            ):
                pass
            service = AccountNamingService(
                accounts_index_path=index,
                user_database_path=database,
                account_id="account-a",
                context_is_current=lambda: True,
            )

            self.assertEqual("新名称", service.rename("  新名称  "))

            saved_index = json.loads(index.read_text(encoding="utf-8"))
            with UserDataDao(database) as user_dao:
                profile = user_dao.profile()
            self.assertEqual("新名称", saved_index["accounts"][0]["name"])
            self.assertEqual("新名称", profile["account_name"])
            self.assertNotIn("export_nickname", profile)

    def test_empty_name_reuses_database_validation_semantics(self) -> None:
        with self.assertRaisesRegex(UserDataValidationError, "account_name 不能为空"):
            AccountNamingService(
                accounts_index_path=Path("unused"),
                user_database_path=Path("unused"),
                account_id="account-a",
                context_is_current=lambda: True,
            ).rename("   ")


if __name__ == "__main__":
    unittest.main()
