# 验证图纸求解使用官方 SQLite 形状和盘面数据，而非旧 JSON 投影。
"""SQLite-backed local blueprint solver regression tests."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class BlueprintSqliteSolverTests(unittest.TestCase):
    def test_official_shape_coordinates_are_normalized_to_solver_matrix(self):
        from src.services.blueprint_service import (
            official_shape_matrix as _official_shape_matrix,
        )

        matrix = _official_shape_matrix({
            "shape_id": "shape-test",
            "cells": [{"x": 0, "y": -1}, {"x": 0, "y": 0}, {"x": 0, "y": 1}],
        })

        self.assertEqual([[1, 1, 1]], matrix)

    def test_official_plan_board_marks_only_plan_cells_as_playable(self):
        from src.services.blueprint_service import (
            official_board as _official_board,
        )

        cells = [
            {"row": row, "column": column}
            for row in range(1, 5)
            for column in range(1, 6)
        ]
        board = _official_board({"character_name_zh": "测试角色", "cells": cells})

        self.assertEqual(0, board[0][0])
        self.assertEqual(-1, board[4][4])

    def test_static_solver_returns_locally_solved_blueprint(self):
        from src.services.blueprint_service import solve_blueprints_from_static
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

        with patch.dict("os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}):
            with StaticGameDataDao() as dao:
                plans = solve_blueprints_from_static(dao)

        self.assertTrue(plans)
        role = next(iter(plans.values()))
        self.assertTrue(role["blueprints"])
        self.assertTrue(all(cell not in ("0", "0.0") for row in role["blueprints"][0]["board"] for cell in row))

    def test_legacy_public_extra_shape_override_cannot_change_blueprint_preference(self):
        from src.services.blueprint_service import solve_blueprints_from_static
        from src.storage.sqlite.shared_data_dao import SharedDataDao
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

        with tempfile.TemporaryDirectory() as directory:
            static_database = Path(directory) / "game_static.sqlite3"
            shared_database = Path(directory) / "app_shared.sqlite3"
            shutil.copy2(STATIC_DATABASE_PATH, static_database)
            with StaticGameDataDao(static_database) as dao:
                character_id = next(
                    int(character["character_id"])
                    for character in dao.list_characters()
                    if dao.get_equipment_plan(int(character["character_id"])) is not None
                )
                baseline = solve_blueprints_from_static(dao)
                logical_key = dao.get_logical_character_key(character_id)
            with SharedDataDao(shared_database) as shared_dao:
                shared_dao.upsert_shape_bonus_override(
                    logical_key,
                    representative_character_id=character_id,
                    shape_label="Type-4",
                    shape_grid_count=4,
                    properties=[{"property_id": "CritBase", "display_value": 8.0}],
                    based_on_dataset_id="legacy-fixture",
                )
            with StaticGameDataDao(static_database) as dao:
                plans = solve_blueprints_from_static(
                    dao,
                    shared_database_path=shared_database,
                )

        baseline_role = next(
            plan for plan in baseline.values() if plan["character_id"] == character_id
        )
        role = next(
            plan for plan in plans.values() if plan["character_id"] == character_id
        )
        self.assertEqual(
            baseline_role["preferred_extra_label"], role["preferred_extra_label"]
        )

    def test_custom_role_uses_its_saved_board_and_target_suit(self):
        import tempfile

        from src.services.blueprint_service import solve_blueprints_from_static
        from src.services.custom_character_service import (
            create_custom_character,
            save_custom_character_target_suit,
        )
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
        from src.storage.sqlite.user_data_dao import UserDataDao

        with tempfile.TemporaryDirectory() as directory:
            user_database = Path(directory) / "user.sqlite3"
            with UserDataDao(user_database, account_id="blueprint-custom"):
                pass
            custom = create_custom_character(user_database, "图纸自建角色")
            with StaticGameDataDao(STATIC_DATABASE_PATH) as static_dao:
                suit = next(
                    row for row in static_dao.list_suits()
                    if row.get("required_shape_ids")
                )
                save_custom_character_target_suit(
                    user_database,
                    int(custom["character_id"]),
                    str(suit["suit_id"]),
                )
                with UserDataDao(user_database) as user_dao:
                    plans = solve_blueprints_from_static(
                        static_dao,
                        custom_characters=user_dao.list_custom_characters(),
                    )

        role = plans["图纸自建角色"]
        self.assertTrue(role["is_custom"])
        self.assertEqual(suit["name_zh"], role["suit_name"])
        self.assertTrue(role["blueprints"])
