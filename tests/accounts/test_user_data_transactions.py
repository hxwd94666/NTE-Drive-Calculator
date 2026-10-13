# 验证 DAO 可组合事务的可见性、保存点失败及外层回退，不依赖界面或真实账号。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao


NTE_TEST_TIER = "core"


class UserDataTransactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.database = Path(self.temporary.name) / "user.sqlite3"
        self.dao = UserDataDao(self.database, account_id="transaction-fixture")
        self.addCleanup(self.dao.close)

    def seed(self, cid: int) -> dict:
        return self.dao.seed_character_weight_preferences(
            cid, properties=[{"property_id": "CritBase", "weight": 1.0}],
            source_dataset_id="fixture", source_kind="account",
        )

    def test_nested_write_is_invisible_until_outer_commit(self) -> None:
        with UserDataDao(self.database) as reader:
            with self.dao.write_transaction():
                self.seed(1001)
                self.assertIsNone(reader.get_character_weight_preferences(1001))
                self.dao.save_character_weight_preferences(
                    1001, properties=[{"property_id": "CritBase", "weight": 2.0}],
                )
                self.assertIsNone(reader.get_character_weight_preferences(1001))
            self.assertEqual(2.0, reader.get_character_weight_preferences(1001)["property_weights"]["CritBase"])

    def test_outer_failure_rolls_back_successful_nested_methods(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "fixture outer failure"):
            with self.dao.write_transaction():
                self.seed(1001)
                self.dao.create_custom_character("Fixture")
                raise RuntimeError("fixture outer failure")
        self.assertIsNone(self.dao.get_character_weight_preferences(1001))
        self.assertEqual([], self.dao.list_custom_characters())
        # 同一 owner 仍可重试，失败事务未遗留在连接上。
        self.seed(1001)
        with UserDataDao(self.database) as reader:
            self.assertIsNotNone(reader.get_character_weight_preferences(1001))

    def test_caught_inner_sql_failure_only_rolls_back_its_savepoint(self) -> None:
        with self.dao.write_transaction():
            self.seed(1001)
            with self.assertRaises(sqlite3.IntegrityError):
                with self.dao.write_transaction() as connection:
                    self.seed(1002)
                    connection.execute(
                        "INSERT INTO character_weight_preference_property(character_id, property_id, weight, main_weight, ordinal) "
                        "VALUES (9999, 'CritBase', 1, 0, 0)"
                    )
            self.seed(1003)
        with UserDataDao(self.database) as reader:
            self.assertIsNotNone(reader.get_character_weight_preferences(1001))
            self.assertIsNone(reader.get_character_weight_preferences(1002))
            self.assertIsNotNone(reader.get_character_weight_preferences(1003))

    def test_noop_refresh_does_not_rollback_outer_changes(self) -> None:
        self.seed(1001)
        with self.dao.write_transaction():
            self.seed(1002)
            refreshed = self.dao.refresh_unmodified_character_weight_preferences(
                1001, properties=[{"property_id": "CritBase", "weight": 3.0}],
                source_dataset_id="new-fixture", source_kind="default",
            )
            self.assertIsNone(refreshed)
        with UserDataDao(self.database) as reader:
            self.assertIsNotNone(reader.get_character_weight_preferences(1002))
            self.assertEqual(1.0, reader.get_character_weight_preferences(1001)["property_weights"]["CritBase"])


if __name__ == "__main__":
    unittest.main()
