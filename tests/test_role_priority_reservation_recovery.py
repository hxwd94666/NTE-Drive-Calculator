# 验证前序同分回填冲突耗尽搜索后仍保持原优先级和合法图纸。
import unittest
from unittest.mock import patch

from src.models.equipment import Drive
from src.optimizer.role_priority_strategy import RolePriorityStrategy


NTE_TEST_TIER = "core"


class RolePriorityReservationRecoveryTests(unittest.TestCase):
    def test_exhausted_search_jointly_preserves_all_prior_slots(self):
        roles = ["High", "A", "B"]
        shapes = [f"C{index}" for index in range(5)]
        strategy = RolePriorityStrategy(
            {role: {"default_set": "Set", "weights": {"攻击力%": 1.0, "暴击率%": 1.0},
                    "extra_shape_label": "2型", "extra_shape_buffs": {"攻击力%": 10.0}}
             for role in roles},
            {"Set": {"shapes": []}},
            {role: [{"set_pieces": [], "extra_pieces": shapes}] for role in roles},
        )
        drives = [
            Drive(
                uid=f"{shape}-{kind}", quality="Gold", area=2,
                shape_id=shape, set_name="Set", main_stats={"攻击力": 1.0, "生命值": 1.0},
                role_scores={"High": 10.0 if kind in ("a", "b") else 1.0,
                             "A": 100.0 if kind in ("a", "b") else 1.0,
                             "B": 100.0 if kind in ("a", "b") else 1.0},
            )
            for shape in shapes for kind in ("a", "b", "y", "z")
        ]
        with patch.object(strategy, "_find_best_group_fit", wraps=strategy._find_best_group_fit) as solve:
            result = strategy.execute(
                {"drives": drives, "all_drives": drives, "tapes": {}},
                roles, {role: "Set" for role in roles},
                priority_groups=[["High"], ["A", "B"]],
            )
        # Ordinary Top-K plans are detected first.  This fixture is repaired
        # during progressive protection, so no expensive final matrix is due.
        self.assertGreater(solve.call_count, 2)
        self.assertLess(solve.call_count, 25)
        self.assertTrue(all("reservation_candidates" not in call.kwargs for call in solve.call_args_list))
        self.assertTrue(all(result[role]["valid"] for role in roles))
        self.assertEqual(result["High"]["score"], 50.0)
        self.assertGreater(sum(result[role]["score"] for role in ("A", "B")), 0.0)
        all_uids = []
        for role in roles:
            assigned = result[role]["assigned_extra_drives"]
            self.assertEqual([drive.shape_id for drive in assigned], shapes)
            self.assertEqual(result[role]["score"], sum(drive.role_scores[role] for drive in assigned))
            all_uids.extend(drive.uid for drive in assigned)
        self.assertEqual(len(all_uids), len(set(all_uids)))

    def test_midway_progressive_failure_uses_full_inventory_type_matching(self):
        strategy = RolePriorityStrategy(
            {
                "High": {"default_set": "Set"},
                "Later": {"default_set": "Set"},
            },
            {"Set": {"shapes": []}},
            {
                "High": [{"set_pieces": [], "extra_pieces": ["X"]}],
                "Later": [{"set_pieces": [], "extra_pieces": ["X", "X"]}],
            },
        )
        drives = [
            Drive(
                uid=uid, quality="Gold", area=1, shape_id="X", set_name="Set",
                main_stats={"攻击力": 1.0, "生命值": 1.0},
                role_scores={"High": high_score, "Later": later_score},
            )
            for uid, high_score, later_score in (
                ("a", 10.0, 100.0),
                ("b", 10.0, 99.0),
                ("c", 1.0, 98.0),
            )
        ]

        with patch.object(strategy, "_find_best_group_fit", wraps=strategy._find_best_group_fit) as solve:
            result = strategy.execute(
                {
                    "drives": drives[:2],
                    "all_drives": drives,
                    "tapes": {},
                    "drive_screen_limit": 1,
                },
                ["High", "Later"], {"High": "Set", "Later": "Set"},
                priority_groups=[["High"], ["Later"]],
            )

        self.assertEqual(
            [drive.uid for drive in result["High"]["assigned_extra_drives"]], ["b"],
        )
        self.assertEqual(
            [drive.uid for drive in result["Later"]["assigned_extra_drives"]], ["a", "c"],
        )
        final_call = solve.call_args_list[-1]
        self.assertEqual(final_call.kwargs["reservation_shapes"], ("X",))
        self.assertEqual(final_call.kwargs["reservation_candidates"], (("a", "b"),))

    def test_final_recovery_keeps_critical_threshold_constraints(self):
        strategy = RolePriorityStrategy(
            {"High": {"default_set": "Set"}, "Later": {"default_set": "Set"}},
            {"Set": {"shapes": []}},
            {
                "High": [{"set_pieces": [], "extra_pieces": ["X"]}],
                "Later": [{"set_pieces": [], "extra_pieces": ["X", "X"]}],
            },
        )
        drives = [
            Drive(
                uid=uid, quality="Gold", area=1, shape_id="X", set_name="Set",
                main_stats={"攻击力": 1.0, "生命值": 1.0},
                sub_stats={"暴击率": crit_rate},
                role_scores={"High": high_score, "Later": later_score},
            )
            for uid, high_score, later_score, crit_rate in (
                ("a", 10.0, 100.0, 5.0),
                ("b", 10.0, 99.0, 5.0),
                ("c", 1.0, 98.0, 0.0),
            )
        ]

        result = strategy.execute(
            {
                "drives": drives[:2],
                "all_drives": drives,
                "tapes": {},
                "drive_screen_limit": 1,
            },
            ["High", "Later"], {"High": "Set", "Later": "Set"},
            crit_priority_modes={"Later": {"crit_threshold": 10.0}},
            priority_groups=[["High"], ["Later"]],
        )

        self.assertEqual(
            [drive.uid for drive in result["High"]["assigned_extra_drives"]], ["b"],
        )
        self.assertEqual(
            [drive.uid for drive in result["Later"]["assigned_extra_drives"]], ["a", "c"],
        )


if __name__ == "__main__":
    unittest.main()
