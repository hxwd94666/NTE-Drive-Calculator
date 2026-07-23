# 验证图纸求解使用官方 SQLite 形状和盘面数据，而非旧 JSON 投影。
"""SQLite-backed local blueprint solver regression tests."""

import unittest
from pathlib import Path
from unittest.mock import patch
import os

from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class BlueprintSqliteSolverTests(unittest.TestCase):
    def test_searching_one_role_expands_all_of_its_blueprints(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from types import SimpleNamespace
        from PySide6.QtGui import QPixmap
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from src.features.blueprints import page

        app = QApplication.instance() or QApplication([])
        content = QWidget()
        host = SimpleNamespace(
            _bp_content_layout=QVBoxLayout(content),
            _bp_data={
                "薄荷": {"suit_name": "森林萤火之心", "blueprints": [
                    {"board": [[0]], "extra_pieces": []} for _ in range(4)
                ]},
                "九原": {"suit_name": "测试套装", "blueprints": [
                    {"board": [[0]], "extra_pieces": []} for _ in range(4)
                ]},
            },
        )
        with patch.object(page, "PuzzleBoardWidget", side_effect=lambda *_args, **_kwargs: QWidget()), \
             patch.object(page, "_get_shape_pixmap", return_value=QPixmap()):
            page._draw_blueprints(host, "薄荷")
            app.processEvents()

        text = " ".join(label.text() for label in content.findChildren(page.QLabel))
        self.assertIn("方案 4", text)
        self.assertNotIn("仅展示前 3 套图纸", text)

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
