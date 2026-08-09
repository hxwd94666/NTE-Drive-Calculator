# 验证同级组暴击约束的分阶段恢复与全图纸重配。
"""Focused coverage for bounded equal-priority critical-rate repair."""

from __future__ import annotations

import unittest

from src.models.equipment import Drive, Tape
from src.optimizer.role_priority_strategy import RolePriorityStrategy


def _drive(uid: str, crit: float) -> Drive:
    return Drive(
        uid=uid,
        quality="Gold",
        area=2,
        shape_id="H_2",
        main_stats={"攻击力": 1.0, "生命值": 1.0},
        sub_stats={"暴击率%": crit},
    )


def _tape(
    uid: str,
    main: str,
    score: float = 0.0,
    *,
    sub_stats: dict[str, float] | None = None,
) -> Tape:
    return Tape(
        uid=uid,
        quality="Gold",
        area=15,
        set_name="Set",
        main_stats=main,
        sub_stats=sub_stats or {"暴击率%": 1.0},
        role_scores={"A": score},
    )


class EqualGroupCritRepairTests(unittest.TestCase):
    def test_equal_priority_phase3b_checks_every_blueprint_for_failed_role(self) -> None:
        strategy = RolePriorityStrategy(
            {
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            {"Set": {"shapes": []}},
            {
                "A": [
                    {"set_pieces": [], "extra_pieces": ["H_2"]},
                    {"set_pieces": [], "extra_pieces": ["H_3"]},
                ],
                "B": [{"set_pieces": [], "extra_pieces": ["H_4"]}],
            },
        )
        over_cap = _drive("a-over-cap", 30.0)
        fitting = Drive(
            uid="a-fitting",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0},
            role_scores={"A": 80.0, "B": 0.0},
        )
        peer = Drive(
            uid="b-peer",
            quality="Gold",
            area=4,
            shape_id="H_4",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0},
            role_scores={"A": 0.0, "B": 80.0},
        )
        over_cap.role_scores = {"A": 100.0, "B": 0.0}
        strategy._find_best_group_fit = lambda group, *_args, **_kwargs: {  # type: ignore[method-assign]
            role: {"valid": False} for role in group
        }

        result = strategy.execute(
            {
                "drives": [over_cap, fitting, peer],
                "all_drives": [over_cap, fitting, peer],
                "tapes": {},
            },
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            priority_groups=[["A", "B"]],
            crit_rate_caps={"A": 20.0},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(["H_3"], result["A"]["blueprint"]["extra_pieces"])
        self.assertEqual(["a-fitting"], [drive.uid for drive in result["A"]["assigned_extra_drives"]])
        self.assertTrue(result["B"]["valid"])

    def test_equal_priority_repairs_cap_by_tape_before_releasing_drives(self) -> None:
        strategy = RolePriorityStrategy(
            {
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            {"Set": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["H_3"]}],
            },
        )
        role_drive = _drive("a-drive", 10.0)
        role_drive.role_scores = {"A": 80.0, "B": 0.0}
        peer = Drive(
            uid="b-peer",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0},
            role_scores={"A": 0.0, "B": 80.0},
        )
        crit = _tape("crit", "暴击率%", score=100.0, sub_stats={"攻击力%": 1.0})
        attack = _tape("attack", "攻击力%", score=80.0, sub_stats={"攻击力%": 1.0})
        strategy._find_best_group_fit = lambda group, *_args, **_kwargs: {  # type: ignore[method-assign]
            role: {"valid": False} for role in group
        }

        result = strategy.execute(
            {
                "drives": [role_drive, peer],
                "all_drives": [role_drive, peer],
                "tapes": {"A": [crit, attack]},
            },
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            priority_groups=[["A", "B"]],
            crit_rate_caps={"A": 20.0},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("attack", result["A"]["assigned_tape"].uid)
        self.assertEqual(["a-drive"], [drive.uid for drive in result["A"]["assigned_extra_drives"]])
        self.assertTrue(result["B"]["valid"])

    def test_equal_priority_phase3b_rejects_low_grade_crit_only_items(self) -> None:
        strategy = RolePriorityStrategy(
            {
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            {"Set": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["H_3"]}],
            },
        )
        good_drive = _drive("a-good", 0.0)
        junk_crit_drive = _drive("a-junk-crit", 25.0)
        peer = Drive(
            uid="b-peer",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0},
            role_scores={"A": 0.0, "B": 80.0},
        )
        good_drive.role_scores = {"A": 20.0, "B": 0.0}
        junk_crit_drive.role_scores = {"A": 1.0, "B": 0.0}
        good_tape = _tape(
            "a-good-tape",
            "攻击力%",
            score=100.0,
            sub_stats={"攻击力%": 1.0},
        )
        junk_crit_tape = _tape(
            "a-junk-crit-tape",
            "暴击率%",
            score=1.0,
            sub_stats={"攻击力%": 1.0},
        )
        strategy._find_best_group_fit = lambda group, *_args, **_kwargs: {  # type: ignore[method-assign]
            role: {"valid": False} for role in group
        }

        result = strategy.execute(
            {
                "drives": [good_drive, junk_crit_drive, peer],
                "all_drives": [good_drive, junk_crit_drive, peer],
                "tapes": {"A": [good_tape, junk_crit_tape]},
            },
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            crit_priority_modes={"A": {"crit_threshold": 30.0}},
            priority_groups=[["A", "B"]],
        )

        self.assertFalse(result["A"]["valid"])
        self.assertNotIn("assigned_tape", result["A"])
        self.assertNotIn("assigned_set_drives", result["A"])
        self.assertNotIn("assigned_extra_drives", result["A"])
        self.assertIn("本次从零重配最高达到", result["A"]["reason"])
        self.assertTrue(result["B"]["valid"])

    def test_equal_priority_phase3b_solves_minimum_and_cap_as_one_interval(self) -> None:
        strategy = RolePriorityStrategy(
            {
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            {"Set": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["H_2", "H_2"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["H_3"]}],
            },
        )
        high = _drive("a-high", 20.0)
        medium = _drive("a-medium", 10.0)
        low = _drive("a-low", 5.0)
        high.role_scores = {"A": 100.0, "B": 0.0}
        medium.role_scores = {"A": 80.0, "B": 0.0}
        low.role_scores = {"A": 70.0, "B": 0.0}
        peer = Drive(
            uid="b-peer",
            quality="Gold",
            area=3,
            shape_id="H_3",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"攻击力%": 1.0},
            role_scores={"A": 0.0, "B": 80.0},
        )
        tape = _tape("attack", "攻击力%", score=100.0, sub_stats={"攻击力%": 1.0})
        strategy._find_best_group_fit = lambda group, *_args, **_kwargs: {  # type: ignore[method-assign]
            role: {"valid": False} for role in group
        }

        result = strategy.execute(
            {
                "drives": [high, medium, low, peer],
                "all_drives": [high, medium, low, peer],
                "tapes": {"A": [tape]},
            },
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            crit_priority_modes={"A": {"crit_threshold": 20.0}},
            priority_groups=[["A", "B"]],
            crit_rate_caps={"A": 25.0},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            {"a-medium", "a-low"},
            {drive.uid for drive in result["A"]["assigned_extra_drives"]},
        )
        self.assertEqual(
            20.0,
            strategy._current_role_crit(
                "A",
                result["A"]["assigned_tape"],
                result["A"]["assigned_extra_drives"],
            ),
        )
        self.assertTrue(result["B"]["valid"])



if __name__ == "__main__":
    unittest.main()
