"""Regression coverage for card-tape candidate retention and cap handling."""

from __future__ import annotations

import unittest

from src.models.equipment import Drive, Tape
from src.optimizer.role_priority_strategy import RolePriorityStrategy
from src.optimizer.scoring import ScoringEngine


def _drive(uid: str, crit: float) -> Drive:
    return Drive(
        uid=uid,
        quality="Gold",
        area=2,
        shape_id="H_2",
        main_stats={"攻击力": 1.0, "生命值": 1.0},
        sub_stats={"暴击率%": crit},
    )


def _tape(uid: str, main: str, score: float = 0.0) -> Tape:
    return Tape(
        uid=uid,
        quality="Gold",
        area=15,
        set_name="Set",
        main_stats=main,
        sub_stats={"暴击率%": 1.0},
        role_scores={"A": score},
    )


class TapeCandidateCoverageTests(unittest.TestCase):
    def test_scoring_retains_best_card_for_each_main_stat_beyond_top_k(self) -> None:
        scoring = ScoringEngine(
            roles_db={
                "A": {
                    "weights": {"暴击率%": 1.0},
                    "main_weights": {"暴击率%": 1.0},
                }
            }
        )
        crit = _tape("crit", "暴击率%")
        attack = _tape("attack", "攻击力%")

        pools = scoring.evaluate_global_inventory(
            [crit, attack],
            tape_top_k_per_set_per_role=1,
        )

        self.assertEqual({"crit", "attack"}, {tape.uid for tape in pools["tapes"]["A"]})

    def test_critical_cap_retries_a_non_critical_tape(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {"A": [{"set_pieces": [], "extra_pieces": ["H_2"]}]},
        )
        crit = _tape("crit", "暴击率%", score=100.0)
        attack = _tape("attack", "攻击力%", score=10.0)

        result = strategy.execute(
            {"drives": [_drive("drive", 10.0)], "tapes": {"A": [crit, attack]}},
            ["A"],
            {"A": "Set"},
            crit_rate_caps={"A": 20.0},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("attack", result["A"]["assigned_tape"].uid)

    def test_critical_cap_reports_the_real_blocker_when_no_tape_can_fit(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {"A": [{"set_pieces": [], "extra_pieces": ["H_2"]}]},
        )
        result = strategy.execute(
            {"drives": [_drive("drive", 30.0)], "tapes": {"A": [_tape("attack", "攻击力%", score=10.0)]}},
            ["A"],
            {"A": "Set"},
            crit_rate_caps={"A": 20.0},
        )

        self.assertFalse(result["A"]["valid"])
        self.assertIn("暴击率上限 20%", result["A"]["reason"])


if __name__ == "__main__":
    unittest.main()
