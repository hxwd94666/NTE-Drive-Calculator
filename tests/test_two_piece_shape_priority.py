"""二件套图纸选择的额外形状加成回归测试。"""

from __future__ import annotations

import unittest

from src.models.equipment import Drive, DriveShape
from src.optimizer.drive_priority_strategy import DrivePriorityStrategy, GlobalOptimalStrategy
from src.optimizer.role_priority_strategy import RolePriorityStrategy
from src.solver.combinatorics import PuzzleCombinatorics


class TwoPieceShapePriorityTests(unittest.TestCase):
    """二件套候选形状本身命中额外形状时，应参与图纸择优。"""

    def _strategy(self, strategy_class):
        strategy = strategy_class(
            roles_db={
                "A": {
                    "default_set": "Set",
                    "weights": {"攻击力%": 1.0},
                    "extra_shape_label": "2型",
                    "extra_shape_buffs": {"攻击力%": 10.0},
                }
            },
            sets_db={"Set": {"shapes": []}},
            blueprints_db={
                "A": [
                    {
                        "set_pieces": ["H_2"],
                        "extra_pieces": [],
                        "set_effect_mode": "two_piece",
                        "board": [[1]],
                    },
                    {
                        "set_pieces": ["H_3"],
                        "extra_pieces": [],
                        "set_effect_mode": "two_piece",
                        "board": [[1]],
                    },
                ]
            },
        )
        strategy.stat_catalog.gold_base_values["攻击力%"] = 1.25
        return strategy

    @staticmethod
    def _drives() -> list[Drive]:
        return [
            Drive(
                uid="visible_better",
                quality="Gold",
                area=3,
                shape_id="H_3",
                set_name="Set",
                main_stats={"攻击力": 1.0, "生命值": 1.0},
                sub_stats={},
                role_scores={"A": 20.0},
            ),
            Drive(
                uid="hidden_better",
                quality="Gold",
                area=2,
                shape_id="H_2",
                set_name="Set",
                main_stats={"攻击力": 1.0, "生命值": 1.0},
                sub_stats={},
                role_scores={"A": 1.0},
            ),
        ]

    def test_all_strategies_use_extra_shape_bonus_for_two_piece_choice(self) -> None:
        for strategy_class in (
            RolePriorityStrategy,
            DrivePriorityStrategy,
            GlobalOptimalStrategy,
        ):
            with self.subTest(strategy=strategy_class.__name__):
                result = self._strategy(strategy_class).execute(
                    {"drives": self._drives(), "tapes": {"A": []}},
                    ["A"],
                    {},
                    crit_priority_modes={},
                )

                self.assertTrue(result["A"]["valid"])
                self.assertEqual(
                    "hidden_better",
                    result["A"]["assigned_set_drives"][0].uid,
                )

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


if __name__ == "__main__":
    unittest.main()
