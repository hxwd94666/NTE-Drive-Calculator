"""用户数据库 v36 nte-core 战报来源迁移的公共行为测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import SCHEMA_VERSION, UserDataDao


class UserDataV36MigrationTests(unittest.TestCase):
    def test_new_database_has_battle_core_provenance_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "user_data.sqlite3"
            with UserDataDao(database) as user_dao:
                columns = {
                    str(row[1])
                    for row in user_dao._db().execute(
                        "PRAGMA table_info(battle_record)"
                    )
                }
                self.assertEqual(
                    {
                        "nte_core_version",
                        "nte_core_protocol_version",
                        "nte_core_data_version",
                        "nte_core_executable_sha256",
                    },
                    {
                        name for name in columns if name.startswith("nte_core_")
                    }
                    - {"nte_core_record_id", "nte_core_contract_version"},
                )
                self.assertNotIn("max_hp_reduction", columns)
                self.assertEqual(
                    SCHEMA_VERSION,
                    user_dao.summary()["schema_version"],
                )


if __name__ == "__main__":
    unittest.main()
