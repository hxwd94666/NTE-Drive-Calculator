# 验证跨账号共享额外形状规则直接写入公共静态库。

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from src.services.character_shape_bonus_service import (
    get_effective_character_shape_bonus,
    save_public_character_shape_bonus,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


PROJECT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class CharacterShapeBonusServiceTests(unittest.TestCase):
    def test_writes_shared_logical_shape_bonus_without_an_account_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "game_static.sqlite3"
            shutil.copy2(PROJECT_DATABASE, database)
            user_database = Path(directory) / "user.sqlite3"
            with UserDataDao(user_database, account_id="shape-only") as user_dao:
                user_dao.seed_character_weight_preferences(
                    1051,
                    source_dataset_id="fixture",
                    source_kind="default",
                    properties=[{
                        "property_id": "CritBase", "weight": 1.0,
                        "main_weight": 1.0,
                    }],
                )
                before = user_dao.get_character_weight_preferences(1051)

            saved = save_public_character_shape_bonus(
                1051,
                shape_label="Type-4",
                property_values={"CritBase": 8.0},
                database_path=database,
            )
            with StaticGameDataDao(database) as dao:
                actual = get_effective_character_shape_bonus(dao, 1051)
                bundled = dao.get_character_shape_bonus(1051)
                template = dao.get_character_graduation_template(1051)

            with UserDataDao(user_database) as user_dao:
                after = user_dao.get_character_weight_preferences(1051)

        self.assertEqual("Type-4", saved["shape_label"])
        self.assertEqual("Type-4", actual["shape_label"])
        self.assertEqual(
            [("CritBase", 8.0)],
            [(row["property_id"], row["display_value"]) for row in actual["properties"]],
        )
        self.assertIsNotNone(template)
        self.assertEqual("default", after["source_kind"])
        self.assertEqual(before["updated_at_utc"], after["updated_at_utc"])
        self.assertEqual("Type-4", bundled["shape_label"])

    def test_keeps_current_public_label_when_a_bonus_only_save_has_empty_label(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "game_static.sqlite3"
            shutil.copy2(PROJECT_DATABASE, database)

            saved = save_public_character_shape_bonus(
                1051,
                shape_label="",
                property_values={"AtkUp": 10.0},
                database_path=database,
            )

        self.assertTrue(saved["shape_label"].startswith("Type-"))
        self.assertEqual(
            [("AtkUp", 10.0)],
            [(row["property_id"], row["display_value"]) for row in saved["properties"]],
        )
