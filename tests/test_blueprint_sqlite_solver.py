# 验证图纸求解使用官方 SQLite 形状和盘面数据，而非旧 JSON 投影。
"""SQLite-backed local blueprint solver regression tests."""

import unittest
from pathlib import Path
from unittest.mock import patch

from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class BlueprintSqliteSolverTests(unittest.TestCase):
    def test_official_shape_coordinates_are_normalized_to_solver_matrix(self):
        from src.features.blueprints.page import _official_shape_matrix

        matrix = _official_shape_matrix({
            "shape_id": "shape-test",
            "cells": [{"x": 0, "y": -1}, {"x": 0, "y": 0}, {"x": 0, "y": 1}],
        })

        self.assertEqual([[1, 1, 1]], matrix)

    def test_official_plan_board_marks_only_plan_cells_as_playable(self):
        from src.features.blueprints.page import _official_board

        cells = [
            {"row": row, "column": column}
            for row in range(1, 5)
            for column in range(1, 6)
        ]
        board = _official_board({"character_name_zh": "测试角色", "cells": cells})

        self.assertEqual(0, board[0][0])
        self.assertEqual(-1, board[4][4])

    def test_static_solver_returns_locally_solved_blueprint(self):
        from src.features.blueprints.page import solve_blueprints_from_static
        from src.storage.sqlite.static_game_data_dao import StaticGameDataDao

        with patch.dict("os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}):
            with StaticGameDataDao() as dao:
                plans = solve_blueprints_from_static(dao)

        self.assertTrue(plans)
        role = next(iter(plans.values()))
        self.assertTrue(role["blueprints"])
        self.assertTrue(all(cell not in ("0", "0.0") for row in role["blueprints"][0]["board"] for cell in row))
