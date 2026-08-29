# 验证账号角色档案显式保存弧盘突破阶段并稳定迁移旧等级。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
    UserDataDao,
    UserDataValidationError,
)


class CharacterProfileForkBreakthroughTests(unittest.TestCase):
    @staticmethod
    def _save_profile(
        dao: UserDataDao,
        *,
        character_id: int,
        fork_level: int | None,
        fork_breakthrough_stage: int | None,
    ) -> dict:
        return dao.save_character_profile(
            character_id=character_id,
            character_level=80,
            breakthrough_stage=6,
            awakening_level=0,
            fork_id="fork_example" if fork_level is not None else None,
            fork_level=fork_level,
            fork_breakthrough_stage=fork_breakthrough_stage,
            fork_refinement_level=1 if fork_level is not None else None,
        )

    def test_round_trip_requires_stage_with_fork_and_clears_it_without_fork(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user_data.sqlite3"
            with UserDataDao(database, account_id="fixture") as dao:
                saved = self._save_profile(
                    dao,
                    character_id=1001,
                    fork_level=70,
                    fork_breakthrough_stage=6,
                )
                self.assertEqual(6, saved["fork_breakthrough_stage"])

                cleared = self._save_profile(
                    dao,
                    character_id=1002,
                    fork_level=None,
                    fork_breakthrough_stage=5,
                )
                self.assertIsNone(cleared["fork_breakthrough_stage"])

                for invalid_stage in (None, -1, 7):
                    with self.subTest(invalid_stage=invalid_stage):
                        with self.assertRaises(UserDataValidationError):
                            self._save_profile(
                                dao,
                                character_id=1010,
                                fork_level=70,
                                fork_breakthrough_stage=invalid_stage,
                            )

    def test_v36_migration_backfills_reproducible_minimum_legal_stage(self):
        expected_by_level = {
            1: 0,
            20: 0,
            21: 1,
            30: 1,
            31: 2,
            70: 5,
            71: 6,
            80: 6,
        }
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy_v36.sqlite3"
            with UserDataDao(database, account_id="legacy") as dao:
                for character_id, (level, stage) in enumerate(
                    expected_by_level.items(), start=2001
                ):
                    self._save_profile(
                        dao,
                        character_id=character_id,
                        fork_level=level,
                        fork_breakthrough_stage=stage,
                    )
                self._save_profile(
                    dao,
                    character_id=2099,
                    fork_level=None,
                    fork_breakthrough_stage=None,
                )

            connection = sqlite3.connect(database)
            connection.execute(
                "ALTER TABLE character_profile DROP COLUMN fork_breakthrough_stage"
            )
            connection.execute(
                "ALTER TABLE battle_character_build_snapshot "
                "DROP COLUMN fork_breakthrough_stage"
            )
            connection.execute(
                "ALTER TABLE battle_character_build_edit "
                "DROP COLUMN fork_breakthrough_stage"
            )
            connection.execute("DROP TABLE battle_inferred_target_snapshot")
            connection.execute("DELETE FROM schema_migration WHERE version >= 37")
            connection.commit()
            connection.close()

            with UserDataDao(database) as migrated:
                self.assertEqual(SCHEMA_VERSION, migrated.summary()["schema_version"])
                for table in (
                    "battle_character_build_snapshot",
                    "battle_character_build_edit",
                ):
                    columns = {
                        row["name"]
                        for row in migrated._db().execute(
                            f"PRAGMA table_info({table})"
                        )
                    }
                    self.assertIn("fork_breakthrough_stage", columns)
                for character_id, (level, expected_stage) in enumerate(
                    expected_by_level.items(),
                    start=2001,
                ):
                    with self.subTest(level=level):
                        profile = migrated.get_character_profile(character_id)
                        self.assertIsNotNone(profile)
                        self.assertEqual(
                            expected_stage,
                            profile["fork_breakthrough_stage"],
                        )
                self.assertIsNone(
                    migrated.get_character_profile(2099)["fork_breakthrough_stage"]
                )


if __name__ == "__main__":
    unittest.main()
