# 同一流程的公共行为回归合并，保留各自数据、失败与回滚断言。
from __future__ import annotations

# 验证角色好感度与觉醒多选属于当前账号并可稳定重载。
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao


class CharacterProfileAdvancementTests(unittest.TestCase):
    def test_round_trip_preserves_likeability_and_selected_awakenings(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user_data.sqlite3"
            with UserDataDao(database, account_id="fixture") as dao:
                saved = dao.save_character_profile(
                    character_id=1072,
                    character_level=80,
                    breakthrough_stage=6,
                    awakening_level=2,
                    selected_awaken_effect_ids=("Effect2", "Effect5"),
                    awakening_selection_initialized=True,
                    likeability_level_10_enabled=True,
                    fork_id=None,
                    fork_level=None,
                    fork_breakthrough_stage=None,
                    fork_refinement_level=None,
                    selected_skill_id="Skill",
                    skill_levels={"Skill": 10},
                )

            self.assertTrue(saved["likeability_level_10_enabled"])
            self.assertTrue(saved["awakening_selection_initialized"])
            self.assertEqual(
                ["Effect2", "Effect5"],
                saved["selected_awaken_effect_ids"],
            )

# 验证账号角色档案显式保存弧盘突破阶段并稳定迁移旧等级。

import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import (
    SCHEMA_VERSION,
    UserDataDao,
    UserDataValidationError,
)
from tests.storage.user_data_migration_helpers import create_user_database_at_version


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
            create_user_database_at_version(database, 36, account_id="legacy")
            connection = sqlite3.connect(database)
            for ordinal, (character_id, (level, _stage)) in enumerate(
                enumerate(expected_by_level.items(), start=2001)
            ):
                connection.execute(
                    """INSERT INTO character_profile(
                           character_id, character_level, breakthrough_stage,
                           awakening_level, fork_id, fork_level,
                           fork_refinement_level, selected_skill_id, ordinal,
                           is_active, created_at_utc, updated_at_utc
                       ) VALUES (?, 80, 6, 0, 'fork_example', ?, 1, NULL, ?, 1, 'now', 'now')""",
                    (character_id, level, ordinal),
                )
            connection.execute(
                """INSERT INTO character_profile(
                       character_id, character_level, breakthrough_stage,
                       awakening_level, fork_id, fork_level,
                       fork_refinement_level, selected_skill_id, ordinal,
                       is_active, created_at_utc, updated_at_utc
                   ) VALUES (2099, 80, 6, 0, NULL, NULL, NULL, NULL, 99, 1, 'now', 'now')"""
            )
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
