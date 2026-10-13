# 验证基础权重整批提交及自建角色初始化在任何一步失败时保持原事实。
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.features.configuration.controller import BasicWeightController
from .basic_weight_fixture import build_basic_weight_static_fixture
from src.domain.basic_weight import BasicWeightDependencies
from src.services.characters.custom_character_service import create_custom_character
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataError, UserDataValidationError

NTE_TEST_TIER = "core"


ROOT = Path(__file__).resolve().parents[2]


class BasicWeightCommitServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.database = self.root / "user.sqlite3"
        self.static = build_basic_weight_static_fixture(self.root / "static" / "game_static.sqlite3")
        with UserDataDao(self.database, account_id="fixture"):
            pass
        self.first = int(create_custom_character(self.database, "Fixture A")["character_id"])
        self.second = int(create_custom_character(self.database, "Fixture B")["character_id"])
        self.controller = BasicWeightController(BasicWeightDependencies(
            account_id="fixture", generation=1, user_database_path=self.database,
            config_dir=ROOT / "config", static_database_path=self.static,
            shared_database_path=self.root / "shared.sqlite3",
        ))
        self.addCleanup(self.controller.close)
        self.data = {
            "A": {"character_id": self.first, "is_custom": True,
                  "weights": {"CritBase": 2.25}, "main_weights": {}},
            "B": {"character_id": self.second, "is_custom": True,
                  "extra_shape_label": "Type-4", "extra_shape_buffs": {"CritBase": 8.0}},
        }

    def stored_weights(self, character_id: int) -> dict:
        with UserDataDao(self.database) as dao:
            return dao.get_character_weight_preferences(character_id) or {}

    def test_late_validation_failure_keeps_first_role_weights(self) -> None:
        before = self.stored_weights(self.first)
        self.data["B"]["extra_shape_buffs"] = {"CritBase": 8.0, "AtkUp": 10.0}
        with self.assertRaises(UserDataValidationError):
            self.controller.save_changes(self.data, {self.first}, {self.second})
        self.assertEqual(before, self.stored_weights(self.first))

    def test_late_sql_failure_rolls_back_all_fields(self) -> None:
        before = self.stored_weights(self.first)
        # 在第二个字段的实际 SQL 写入制造失败，防止只验证“先校验”而漏掉事务回退。
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "CREATE TRIGGER fixture_shape_failure BEFORE INSERT ON custom_character_shape_bonus "
                "BEGIN SELECT RAISE(ABORT, 'fixture SQL failure'); END"
            )
        with self.assertRaises(UserDataError):
            self.controller.save_changes(self.data, {self.first}, {self.second})
        self.assertEqual(before, self.stored_weights(self.first))
        with UserDataDao(self.database) as dao:
            self.assertEqual([], dao.get_custom_character_shape_bonus(self.second)["properties"])

    def test_custom_role_and_seed_are_created_atomically(self) -> None:
        with UserDataDao(self.database) as dao:
            before = dao.list_custom_characters()
        with patch.object(UserDataDao, "seed_character_weight_preferences", side_effect=RuntimeError("fixture seed failure")):
            with self.assertRaisesRegex(RuntimeError, "fixture seed failure"):
                create_custom_character(self.database, "Interrupted fixture")
        with UserDataDao(self.database) as dao:
            self.assertEqual(before, dao.list_custom_characters())

    def test_multi_role_reset_sql_failure_keeps_first_override(self) -> None:
        with StaticGameDataDao(self.static) as static:
            ids = [int(row["character_id"]) for row in static.list_role_template_characters()
                   if int(row["character_id"]) not in (1046, 1051)][:2]
        self.assertEqual(2, len(ids))
        with UserDataDao(self.database) as dao:
            for cid in ids:
                dao.seed_character_weight_preferences(
                    cid, properties=[{"property_id": "CritBase", "weight": 3.25}],
                    source_dataset_id="fixture", source_kind="account",
                )
        before = self.stored_weights(ids[0])
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                f"CREATE TRIGGER fixture_reset_failure BEFORE UPDATE ON character_weight_preference_seed "
                f"WHEN NEW.character_id = {ids[1]} BEGIN SELECT RAISE(ABORT, 'fixture reset failure'); END"
            )
        with self.assertRaises(UserDataError):
            self.controller.reset_weights(ids)
        self.assertEqual(before, self.stored_weights(ids[0]))

    def test_success_commits_weights_board_suit_and_shape_together(self) -> None:
        with UserDataDao(self.database) as dao:
            cells = dao.list_custom_character_board_cells(self.second)
        edited = [{"row": cell["row_number"], "column": cell["column_number"],
                   "is_enabled": bool(cell["is_enabled"]), "is_locked": bool(cell["is_locked"])}
                  for cell in cells]
        edited[19]["is_enabled"] = False
        edited[20]["is_enabled"] = True
        edited[20]["is_locked"] = True
        with StaticGameDataDao(self.static) as static:
            suit = str(static.list_suits()[0]["suit_id"])
        self.data["B"].update(board_cells=edited, target_suit_id=suit)
        self.controller.save_changes(self.data, {self.first}, {self.second}, {self.second}, {self.second})
        with UserDataDao(self.database) as dao:
            self.assertEqual(2.25, dao.get_character_weight_preferences(self.first)["property_weights"]["CritBase"])
            self.assertEqual(suit, dao.get_custom_character_target_suit_id(self.second))
            self.assertEqual([{"property_id": "CritBase", "display_value": 8.0}],
                             dao.get_custom_character_shape_bonus(self.second)["properties"])
            stored = dao.list_custom_character_board_cells(self.second)
            self.assertFalse(stored[19]["is_enabled"])
            self.assertTrue(stored[20]["is_enabled"] and stored[20]["is_locked"])

    def test_missing_dirty_role_is_rejected_without_partial_save(self) -> None:
        before = self.stored_weights(self.first)
        with self.assertRaises(UserDataValidationError):
            self.controller.save_changes(self.data, {self.first, self.second + 1}, set())
        self.assertEqual(before, self.stored_weights(self.first))

    def test_nonfinite_shape_is_rejected_before_any_write(self) -> None:
        before = self.stored_weights(self.first)
        self.data["B"]["extra_shape_buffs"] = {"CritBase": float("nan")}
        with self.assertRaises(UserDataValidationError):
            self.controller.save_changes(self.data, {self.first}, {self.second})
        self.assertEqual(before, self.stored_weights(self.first))

    def test_invalid_board_coordinates_keep_all_fields(self) -> None:
        with UserDataDao(self.database) as dao:
            cells = dao.list_custom_character_board_cells(self.second)
        board = [{"row": cell["row_number"], "column": cell["column_number"],
                  "is_enabled": bool(cell["is_enabled"])} for cell in cells]
        board[-1]["row"] = 6
        self.data["B"]["board_cells"] = board
        before = self.stored_weights(self.first)
        with self.assertRaises(UserDataValidationError):
            self.controller.save_changes(self.data, {self.first}, set(), {self.second})
        self.assertEqual(before, self.stored_weights(self.first))


if __name__ == "__main__":
    unittest.main()
