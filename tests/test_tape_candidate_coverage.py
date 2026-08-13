# 测试卡带候选保留、暴击上限与兜底覆盖。
"""Regression coverage for card-tape candidate retention and cap handling."""

from __future__ import annotations

import unittest

from src.models.equipment import Drive, Tape
from src.optimizer.allocation_kernel import estimate_candidate_pool_limits
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


class TapeCandidateCoverageTests(unittest.TestCase):
    def test_cartridge_score_uses_the_exact_main_stat_value_when_available(self) -> None:
        scoring = ScoringEngine(roles_db={})
        catalog_value = float(scoring.stat_catalog.tape_main_values["攻击力%"])
        tape = Tape(
            uid="exact-main",
            quality="Gold",
            area=15,
            set_name="Set",
            main_stats="攻击力%",
            main_value=catalog_value / 2.0,
            sub_stats={},
        )

        assert scoring.calculate_cartridge_score(tape, {}, 1.0, {"攻击力%": 1.0}) == 25.0

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

    def test_critical_cap_includes_fixed_five_percent_base(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {"A": [{"set_pieces": [], "extra_pieces": ["H_2"]}]},
        )

        result = strategy.execute(
            {
                "drives": [_drive("drive", 16.0)],
                "tapes": {
                    "A": [
                        _tape(
                            "attack",
                            "攻击力%",
                            score=10.0,
                            sub_stats={"攻击力%": 1.0},
                        )
                    ]
                },
            },
            ["A"],
            {"A": "Set"},
            crit_rate_caps={"A": 20.0},
        )

        self.assertFalse(result["A"]["valid"])
        self.assertIn("暴击率上限 20%", result["A"]["reason"])

    def test_minimum_crit_invalidates_plan_and_reports_threshold(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {"A": [{"set_pieces": [], "extra_pieces": ["H_2"]}]},
        )

        result = strategy.execute(
            {"drives": [_drive("drive", 10.0)], "tapes": {"A": []}},
            ["A"],
            {"A": "Set"},
            crit_priority_modes={"A": {"crit_threshold": 20.0}},
        )

        self.assertFalse(result["A"]["valid"])
        self.assertEqual(
            "没有达成暴击率最小值 20% 的方案（当前 15%）",
            result["A"]["reason"],
        )

    def test_minimum_crit_retries_alternative_tapes(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {"A": [{"set_pieces": [], "extra_pieces": ["H_2"]}]},
        )
        attack = _tape(
            "attack",
            "攻击力%",
            score=100.0,
            sub_stats={"攻击力%": 1.0},
        )
        crit = _tape(
            "crit",
            "暴击率%",
            score=10.0,
            sub_stats={"攻击力%": 1.0},
        )

        result = strategy.execute(
            {"drives": [_drive("drive", 0.0)], "tapes": {"A": [attack, crit]}},
            ["A"],
            {"A": "Set"},
            crit_priority_modes={"A": {"crit_threshold": 35.0}},
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual("crit", result["A"]["assigned_tape"].uid)

    def test_minimum_crit_invalidates_equal_priority_group_roles(self) -> None:
        strategy = RolePriorityStrategy(
            {
                "A": {"default_set": "Set"},
                "B": {"default_set": "Set"},
            },
            {"Set": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
            },
        )
        drives = [_drive("drive-a", 0.0), _drive("drive-b", 0.0)]
        for drive in drives:
            drive.role_scores = {"A": 1.0, "B": 1.0}

        result = strategy.execute(
            {"drives": drives, "all_drives": drives, "tapes": {}},
            ["A", "B"],
            {"A": "Set", "B": "Set"},
            crit_priority_modes={
                "A": {"crit_threshold": 10.0},
                "B": {"crit_threshold": 10.0},
            },
            priority_groups=[["A", "B"]],
        )

        self.assertFalse(result["A"]["valid"])
        self.assertFalse(result["B"]["valid"])
        self.assertIn("没有达成暴击率最小值 10% 的方案", result["A"]["reason"])
        self.assertIn("没有达成暴击率最小值 10% 的方案", result["B"]["reason"])

    def test_ordered_substats_keep_deeper_tape_pool_ahead_of_raw_score(self) -> None:
        scoring = ScoringEngine(
            roles_db={
                "安魂曲": {
                    "weights": {
                        "暴击率%": 1.0,
                        "暴击伤害%": 1.0,
                        "伤害增加%": 0.75,
                        "攻击力%": 0.65,
                        "环合强度": 0.2,
                    },
                    "main_weights": {"暴击率%": 1.0},
                }
            }
        )
        damage_tape = _tape(
            "double-crit-damage",
            "暴击率%",
            sub_stats={"暴击率%": 1.0, "暴击伤害%": 1.0, "伤害增加%": 1.0},
        )
        attack_mag_tape = _tape(
            "double-crit-attack-mag",
            "暴击率%",
            sub_stats={
                "暴击率%": 1.0,
                "暴击伤害%": 1.0,
                "攻击力%": 1.0,
                "环合强度": 1.0,
            },
        )
        preference = {
            "stats": ["暴击率%", "暴击伤害%", "伤害增加%", "攻击力%"],
            "ignore_grade_limit": True,
        }

        pools = scoring.evaluate_global_inventory(
            [damage_tape, attack_mag_tape],
            tape_top_k_per_set_per_role=1,
            crit_priority_modes={"安魂曲": preference},
        )

        self.assertGreater(
            attack_mag_tape.role_scores["安魂曲"],
            damage_tape.role_scores["安魂曲"],
        )
        self.assertEqual(
            {"double-crit-damage", "double-crit-attack-mag"},
            {tape.uid for tape in pools["tapes"]["安魂曲"]},
        )

        strategy = RolePriorityStrategy(
            {"安魂曲": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {},
        )
        assigned = strategy._pre_allocate_tapes(
            ["安魂曲"],
            {"安魂曲": "Set"},
            {"安魂曲": [damage_tape, attack_mag_tape]},
            {"安魂曲": preference},
        )
        self.assertEqual("double-crit-damage", assigned["安魂曲"].uid)

    def test_zero_score_priority_keeps_matching_and_full_fallback_tapes(self) -> None:
        scoring = ScoringEngine(
            roles_db={"A": {"weights": {"暴击率%": 0.0}, "main_weights": {}}}
        )
        matching = _tape(
            "matching",
            "防御力%",
            sub_stats={"暴击率%": 1.0},
        )
        nonmatching = _tape(
            "nonmatching",
            "防御力%",
            sub_stats={"攻击力%": 1.0},
        )

        pools = scoring.evaluate_global_inventory(
            [matching, nonmatching],
            crit_priority_modes={
                "A": {
                    "stats": ["暴击率%"],
                    "ignore_grade_limit": True,
                }
            },
        )

        self.assertEqual(0.0, matching.role_scores["A"])
        self.assertEqual(
            {"matching", "nonmatching"},
            {tape.uid for tape in pools["tapes"]["A"]},
        )

    def test_core_main_filter_stays_hard_before_substat_preference(self) -> None:
        scoring = ScoringEngine(
            roles_db={
                "A": {
                    "weights": {"暴击率%": 1.0, "暴击伤害%": 1.0},
                    "main_weights": {"暴击率%": 1.0, "攻击力%": 1.0},
                }
            }
        )
        allowed_main = _tape(
            "allowed-main",
            "暴击率%",
            sub_stats={"暴击率%": 1.0},
        )
        deeper_wrong_main = _tape(
            "wrong-main",
            "攻击力%",
            sub_stats={"暴击率%": 1.0, "暴击伤害%": 1.0},
        )

        pools = scoring.evaluate_global_inventory(
            [allowed_main, deeper_wrong_main],
            tape_main_filters={"A": ["暴击率%"]},
            crit_priority_modes={
                "A": {
                    "stats": ["暴击率%", "暴击伤害%"],
                    "ignore_grade_limit": True,
                }
            },
        )

        self.assertEqual(["allowed-main"], [tape.uid for tape in pools["tapes"]["A"]])

    def test_drive_pick_filters_to_deepest_available_pool_before_score(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {},
        )
        deeper = Drive(
            uid="abc",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"a": 1.0, "b": 1.0, "c": 1.0},
            role_scores={"A": 1.0},
        )
        shallower = Drive(
            uid="ab",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"a": 1.0, "b": 1.0},
            role_scores={"A": 1000.0},
        )

        picked = strategy._pick_best_drive(
            "A",
            [(0, shallower), (1, deeper)],
            {"stats": ["a", "b", "c"], "ignore_grade_limit": True},
        )

        self.assertIsNotNone(picked)
        self.assertEqual("abc", picked[1].uid)

    def test_repeated_shape_slots_fall_back_depth_by_depth_then_full_pool(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {},
        )

        def candidate(uid: str, sub_stats: dict[str, float], score: float) -> Drive:
            return Drive(
                uid=uid,
                quality="Gold",
                area=2,
                shape_id="H_2",
                main_stats={"攻击力%": 1.0, "生命值%": 1.0},
                sub_stats=sub_stats,
                role_scores={"A": score},
            )

        deepest = candidate("abc", {"a": 1.0, "b": 1.0, "c": 1.0}, 1.0)
        outer = candidate("ab", {"a": 1.0, "b": 1.0}, 10.0)
        full = candidate("none", {"z": 1.0}, 100.0)

        plan = strategy._find_best_fit(
            "A",
            {"set_pieces": [], "extra_pieces": ["H_2", "H_2", "H_2"]},
            [full, outer, deepest],
            "Set",
            {"stats": ["a", "b", "c"], "ignore_grade_limit": True},
        )

        self.assertTrue(plan["valid"])
        self.assertEqual(
            ["abc", "ab", "none"],
            [drive.uid for drive in plan["assigned_extra_drives"]],
        )

    def test_substat_blacklist_hard_filters_drives_but_not_tapes(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {},
        )
        config = {
            "stats": ["wanted"],
            "blacklist": ["blocked"],
            "ignore_grade_limit": True,
        }
        blocked_drive = Drive(
            uid="blocked-drive",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"wanted": 1.0, "blocked": 1.0},
            role_scores={"A": 1000.0},
        )
        allowed_drive = Drive(
            uid="allowed-drive",
            quality="Gold",
            area=2,
            shape_id="H_2",
            main_stats={"攻击力": 1.0, "生命值": 1.0},
            sub_stats={"wanted": 1.0},
            role_scores={"A": 1.0},
        )
        blocked_tape = _tape(
            "blocked-tape",
            "暴击率%",
            sub_stats={"wanted": 1.0, "blocked": 1.0},
        )
        allowed_tape = _tape(
            "allowed-tape",
            "暴击率%",
            sub_stats={"wanted": 1.0},
        )
        blocked_tape.role_scores = {"A": 1000.0}
        allowed_tape.role_scores = {"A": 1.0}

        picked = strategy._pick_best_drive(
            "A",
            [(0, blocked_drive), (1, allowed_drive)],
            config,
        )
        assigned = strategy._pre_allocate_tapes(
            ["A"],
            {"A": "Set"},
            {"A": [blocked_tape, allowed_tape]},
            {"A": config},
        )
        _slots, profit_matrix, ranking_matrix = strategy._build_profit_matrix(
            [{"set_pieces": [], "extra_pieces": ["H_2"]}],
            ["A"],
            [blocked_drive, allowed_drive],
            {"A": "Set"},
            {"A": config},
        )

        self.assertIsNotNone(picked)
        self.assertEqual("allowed-drive", picked[1].uid)
        self.assertEqual("blocked-tape", assigned["A"].uid)
        self.assertEqual(-10000.0, profit_matrix[0, 0])
        self.assertEqual(-10000.0, ranking_matrix[0, 0])
        self.assertEqual(1.0, profit_matrix[0, 1])

    def test_seven_strict_roles_keep_seven_distinct_shared_tape_candidates(self) -> None:
        role_names = [f"R{index}" for index in range(7)]
        roles = {
            role: {
                "default_set": "Set",
                "weights": {"暴击率%": 1.0},
                "main_weights": {"暴击率%": 1.0},
            }
            for role in role_names
        }
        tapes = [
            Tape(
                uid=f"tape-{index}",
                quality="Gold",
                area=15,
                set_name="Set",
                main_stats="暴击率%",
                sub_stats={"暴击率%": 1.0},
            )
            for index in range(7)
        ]
        _drive_limit, tape_limit = estimate_candidate_pool_limits(
            {},
            role_names,
            [[role] for role in role_names],
        )
        pools = ScoringEngine(roles_db=roles).evaluate_global_inventory(
            tapes,
            tape_top_k_per_set_per_role=tape_limit,
        )
        strategy = RolePriorityStrategy(
            roles,
            {"Set": {"shapes": []}},
            {},
        )

        assigned = strategy._pre_allocate_tapes_for_groups(
            [[role] for role in role_names],
            {role: "Set" for role in role_names},
            pools["tapes"],
        )

        self.assertEqual(7, len({tape.uid for tape in assigned.values() if tape}))

    def test_single_role_matches_projected_tape_by_official_suit_id(self) -> None:
        role = "A"
        tape = Tape(
            uid="sound",
            quality="Purple",
            area=15,
            suit_id="Suit11",
            set_name="音速蓝刺猬",
            main_stats="攻击力%",
            sub_stats={"攻击力%": 1.0},
            role_scores={role: 10.0},
        )
        strategy = RolePriorityStrategy(
            {role: {"default_set": "「音速蓝刺猬」"}},
            {"「音速蓝刺猬」": {"suit_id": "Suit11", "shapes": []}},
            {},
        )

        assigned = strategy._pre_allocate_tapes_for_groups(
            [[role]],
            {},
            {role: [tape]},
        )

        self.assertIs(tape, assigned[role])
        self.assertEqual("音速蓝刺猬", tape.set_name)

    def test_official_suit_id_wins_over_an_equal_display_name(self) -> None:
        role = "A"
        wrong_suit = Tape(
            uid="wrong",
            quality="Purple",
            area=15,
            suit_id="Suit99",
            set_name="音速蓝刺猬",
            main_stats="攻击力%",
            sub_stats={"攻击力%": 1.0},
            role_scores={role: 10.0},
        )
        strategy = RolePriorityStrategy(
            {role: {"default_set": "「音速蓝刺猬」"}},
            {"「音速蓝刺猬」": {"suit_id": "Suit11", "shapes": []}},
            {},
        )

        assigned = strategy._pre_allocate_tapes_for_groups(
            [[role]],
            {},
            {role: [wrong_suit]},
        )

        self.assertIsNone(assigned[role])

    def test_legacy_tape_without_suit_id_uses_normalized_name_fallback(self) -> None:
        role = "A"
        legacy_tape = Tape(
            uid="legacy",
            quality="Purple",
            area=15,
            set_name="音速蓝刺猬",
            main_stats="攻击力%",
            sub_stats={"攻击力%": 1.0},
            role_scores={role: 10.0},
        )
        strategy = RolePriorityStrategy(
            {role: {"default_set": "「音速蓝刺猬」"}},
            {"「音速蓝刺猬」": {"suit_id": "Suit11", "shapes": []}},
            {},
        )

        assigned = strategy._pre_allocate_tapes_for_groups(
            [[role]],
            {},
            {role: [legacy_tape]},
        )

        self.assertIs(legacy_tape, assigned[role])

    def test_legacy_tape_name_can_match_an_official_id_target(self) -> None:
        role = "A"
        legacy_tape = Tape(
            uid="legacy-id-target",
            quality="Purple",
            area=15,
            set_name="音速蓝刺猬",
            main_stats="攻击力%",
            sub_stats={"攻击力%": 1.0},
            role_scores={role: 10.0},
        )
        strategy = RolePriorityStrategy(
            {role: {"default_set": "「音速蓝刺猬」"}},
            {"「音速蓝刺猬」": {"suit_id": "Suit11", "shapes": []}},
            {},
            core_set_targets={role: "Suit11"},
        )

        assigned = strategy._pre_allocate_tapes_for_groups(
            [[role]],
            {},
            {role: [legacy_tape]},
        )

        self.assertIs(legacy_tape, assigned[role])


if __name__ == "__main__":
    unittest.main()
