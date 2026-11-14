# 验证二件套图纸的额外形状层次与必要槽身份。
"""二件套图纸候选规则回归测试。"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.models.equipment import DriveShape
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.solver.orchestrator import NTEPipelineOrchestrator


class TwoPieceShapePriorityTests(unittest.TestCase):
    """优先层保留必要槽身份，缺件时仍可检查低层。"""

    def test_none_mode_can_use_other_shape_to_fill_extra_shape_remainder(self) -> None:
        shapes = {
            "Extra3": DriveShape(
                shape_id="Extra3", label="3型", matrix=[[1, 1, 1]], area=3,
            ),
            "Other2": DriveShape(
                shape_id="Other2", label="2型", matrix=[[1, 1]], area=2,
            ),
        }

        combinations = PuzzleCombinatorics(shapes).generate_piece_combinations([], "3型")

        self.assertEqual([["Extra3"] * 6 + ["Other2"]], combinations)

    def test_two_piece_keeps_lower_extra_count_as_fallback(self) -> None:
        shapes = {
            "Extra3": DriveShape(
                shape_id="Extra3", label="3型", matrix=[[1, 1, 1]], area=3,
            ),
            "Other2": DriveShape(
                shape_id="Other2", label="2型", matrix=[[1, 1]], area=2,
            ),
        }
        combinatorics = PuzzleCombinatorics(shapes)

        strict = combinatorics.generate_piece_combinations(["Other2"], "3型")
        relaxed = combinatorics.generate_piece_combinations(
            ["Other2"], "3型", only_max_extra=False,
        )

        self.assertEqual(strict, relaxed[:len(strict)])
        self.assertGreater(len(relaxed), len(strict))

    def test_identical_total_shapes_preserve_distinct_two_piece_set_pairs(self) -> None:
        blueprints = [
            {"set_effect_mode": "two_piece", "set_pieces": ["A", "B"], "extra_pieces": ["C", "D"]},
            {"set_effect_mode": "two_piece", "set_pieces": ["A", "C"], "extra_pieces": ["B", "D"]},
        ]

        self.assertEqual(blueprints, dedupe_blueprints_by_piece_signature(blueprints))

    def test_equal_area_set_pairs_reuse_fill_combinations(self) -> None:
        shapes = {
            name: DriveShape(shape_id=name, label="1型", matrix=[[1]], area=1)
            for name in ("A", "B", "C", "D")
        }
        solver = NTEPipelineOrchestrator.from_frozen_inputs(
            roles_db={"Role": {
                "default_set": "Set", "extra_shape_label": "1型",
                "board_matrix": [[0, 0]],
            }},
            sets_db={"Set": {"shapes": ["A", "B", "C", "D"]}},
            shapes_db=shapes,
        )
        with patch.object(
            PuzzleCombinatorics, "generate_piece_combinations", return_value=[[]],
        ) as generate:
            blueprints = solver.solve_blueprints(
                ["Role"], set_effect_modes={"Role": "two_piece"},
            )["Role"]

        self.assertEqual(1, generate.call_count)
        self.assertIs(generate.call_args.kwargs["only_max_extra"], False)
        self.assertEqual(6, len(blueprints))
        self.assertIsNot(blueprints[0]["extra_pieces"], blueprints[1]["extra_pieces"])

    def test_four_piece_and_no_effect_keep_maximum_extra_count_gate(self) -> None:
        shapes = {
            name: DriveShape(shape_id=name, label="1型", matrix=[[1]], area=1)
            for name in ("A", "B", "C", "D")
        }
        for mode in ("four_piece", "none"):
            with self.subTest(mode=mode):
                solver = NTEPipelineOrchestrator.from_frozen_inputs(
                    roles_db={"Role": {
                        "default_set": "Set", "extra_shape_label": "1型",
                        "board_matrix": [[0, 0]],
                    }},
                    sets_db={"Set": {"shapes": ["A", "B", "C", "D"]}},
                    shapes_db=shapes,
                )
                with patch.object(
                    PuzzleCombinatorics, "generate_piece_combinations", return_value=[[]],
                ) as generate:
                    solver.solve_blueprints(["Role"], set_effect_modes={"Role": mode})

                self.assertEqual(1, generate.call_count)
                self.assertIs(generate.call_args.kwargs["only_max_extra"], True)


if __name__ == "__main__":
    unittest.main()
