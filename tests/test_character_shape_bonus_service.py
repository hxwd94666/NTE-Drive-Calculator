# 验证额外形状公共覆盖与发行静态默认严格分层。

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from src.services.character_shape_bonus_service import (
    get_effective_character_shape_bonus,
    reset_public_character_shape_bonus,
    save_public_character_shape_bonus,
)
from src.storage.sqlite.shared_data_dao import SharedDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


PROJECT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class CharacterShapeBonusServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.static_database = root / "game_static.sqlite3"
        self.shared_database = root / "app_shared.sqlite3"
        shutil.copy2(PROJECT_DATABASE, self.static_database)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def effective(self, character_id: int = 1051):
        with StaticGameDataDao(self.static_database) as static_dao:
            return get_effective_character_shape_bonus(
                static_dao,
                character_id,
                shared_database_path=self.shared_database,
            )

    def test_empty_shared_database_falls_back_to_static_default(self) -> None:
        with SharedDataDao(self.shared_database):
            pass
        effective = self.effective()

        self.assertEqual("static_default", effective["effective_source"])
        self.assertTrue(effective["shape_label"].startswith("Type-"))
        with SharedDataDao(self.shared_database) as shared_dao:
            self.assertIsNone(
                shared_dao.get_shape_bonus_override(
                    effective["logical_character_key"]
                )
            )

    def test_create_and_update_override_without_writing_static_database(self) -> None:
        before_hash = _sha256(self.static_database)
        original_default = self.effective()

        created = save_public_character_shape_bonus(
            1051,
            shape_label="Type-4",
            property_values={"CritBase": 8.0},
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )
        updated = save_public_character_shape_bonus(
            1051,
            shape_label="Type-2",
            property_values={"AtkUp": 11.0},
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )

        self.assertEqual("shared_override", created["effective_source"])
        self.assertEqual("Type-2", updated["shape_label"])
        self.assertEqual(
            [("AtkUp", 11.0)],
            [
                (row["property_id"], row["display_value"])
                for row in updated["properties"]
            ],
        )
        self.assertEqual(before_hash, _sha256(self.static_database))
        with StaticGameDataDao(self.static_database) as static_dao:
            bundled = static_dao.get_character_shape_bonus(1051)
        self.assertEqual(original_default["shape_label"], bundled["shape_label"])

    def test_delete_override_immediately_restores_new_static_default(self) -> None:
        save_public_character_shape_bonus(
            1051,
            shape_label="Type-4",
            property_values={"CritBase": 8.0},
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )

        restored = reset_public_character_shape_bonus(
            1051,
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )

        self.assertEqual("static_default", restored["effective_source"])
        self.assertNotEqual("Type-4", restored["shape_label"])

    def test_static_default_updates_only_affect_roles_without_override(self) -> None:
        with closing(sqlite3.connect(self.static_database)) as connection:
            with connection:
                connection.execute(
                    """UPDATE logical_character_shape_bonus
                       SET shape_label = 'Type-4', shape_grid_count = 4
                       WHERE logical_character_key = 'protagonist'"""
                )
        self.assertEqual("Type-4", self.effective()["shape_label"])
        save_public_character_shape_bonus(
            1051,
            shape_label="Type-2",
            property_values={"CritBase": 8.0},
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )
        with closing(sqlite3.connect(self.static_database)) as connection:
            with connection:
                connection.execute(
                    """UPDATE logical_character_shape_bonus
                       SET shape_label = 'Type-3', shape_grid_count = 3
                       WHERE logical_character_key = 'protagonist'"""
                )

        effective = self.effective()

        self.assertEqual("Type-2", effective["shape_label"])
        self.assertEqual("shared_override", effective["effective_source"])

    def test_override_is_shared_across_accounts_and_accounts_cannot_pollute_it(self) -> None:
        root = Path(self.temporary.name)
        for account_id in ("account-a", "account-b"):
            with UserDataDao(
                root / account_id / "user_data.sqlite3",
                account_id=account_id,
            ):
                pass
        saved = save_public_character_shape_bonus(
            1051,
            shape_label="Type-4",
            property_values={"CritBase": 8.0},
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )
        before = self.shared_database.read_bytes()
        with UserDataDao(root / "account-a" / "user_data.sqlite3") as user_dao:
            user_dao.seed_character_weight_preferences(
                1051,
                source_dataset_id="fixture",
                source_kind="default",
                properties=[
                    {
                        "property_id": "CritBase",
                        "weight": 1.0,
                        "main_weight": 1.0,
                    }
                ],
            )

        account_a_effective = self.effective()
        account_b_effective = self.effective()

        self.assertEqual(saved, account_a_effective)
        self.assertEqual(account_a_effective, account_b_effective)
        self.assertEqual(before, self.shared_database.read_bytes())

    def test_empty_label_keeps_current_static_label_for_new_override(self) -> None:
        default = self.effective()

        saved = save_public_character_shape_bonus(
            1051,
            shape_label="",
            property_values={"AtkUp": 10.0},
            database_path=self.static_database,
            shared_database_path=self.shared_database,
        )

        self.assertEqual(default["shape_label"], saved["shape_label"])
        self.assertEqual(
            [("AtkUp", 10.0)],
            [
                (row["property_id"], row["display_value"])
                for row in saved["properties"]
            ],
        )


if __name__ == "__main__":
    unittest.main()
