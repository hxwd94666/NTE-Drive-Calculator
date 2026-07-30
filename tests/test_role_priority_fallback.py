# 验证缺失卡带与平级角色候选兜底的分配规则。
"""Regression coverage for missing-core and equal-priority drive allocation."""

from __future__ import annotations

import unittest

from src.models.equipment import Drive, Tape
from src.optimizer.allocation_kernel import (
    AllocationKernel,
    AllocationKernelRequest,
    estimate_candidate_pool_limits,
)
from src.optimizer.role_priority_strategy import RolePriorityStrategy


def _drive(uid: str, shape_id: str = "H_2") -> Drive:
    return Drive(
        uid=uid,
        quality="Gold",
        area=2,
        shape_id=shape_id,
        main_stats={"攻击力": 1.0, "防御力": 1.0},
    )


def _plan(drive: Drive) -> dict:
    return {
        "valid": True,
        "blueprint": {"set_pieces": ["H_2"], "extra_pieces": []},
        "assigned_tape": None,
        "assigned_set_drives": [drive],
        "assigned_extra_drives": [],
        "score": 1.0,
    }


def _tape(uid: str, *, set_name: str = "S", main_stat: str = "攻击力%") -> Tape:
    return Tape(
        uid=uid,
        quality="Gold",
        area=15,
        set_name=set_name,
        main_stats=main_stat,
        sub_stats={"暴击率%": 1.0},
    )


class RolePriorityFallbackTests(unittest.TestCase):
    def test_missing_core_never_invalidates_a_complete_drive_blueprint(self) -> None:
        drive = _drive("drive-1")
        request = AllocationKernelRequest(
            inventory=(drive,), roles_db={"A": {}}, sets_db={}, shapes_db={},
            blueprints_db={}, role_order=("A",), strategy="role_priority",
            module_set_targets={}, set_effect_modes={}, core_main_filters={},
            core_set_targets={}, stat_priority_configs={}, property_limits={},
            allow_missing_core=False,
        )
        kernel = AllocationKernel(None)  # _invalid_roles does not score without limits.

        self.assertEqual((), kernel._invalid_roles(request, {"A": _plan(drive)}))
        self.assertEqual(
            ("A",),
            kernel._invalid_roles(
                request,
                {
                    "A": {
                        **_plan(drive),
                        "assigned_set_drives": [],
                    }
                },
            ),
        )

    def test_missing_core_diagnostic_distinguishes_supply_shortage(self) -> None:
        drive_a = _drive("drive-a")
        drive_b = _drive("drive-b")
        only_core = _tape("core-1")
        request = AllocationKernelRequest(
            inventory=(drive_a, drive_b, only_core),
            roles_db={
                "A": {"default_set": "S"},
                "B": {"default_set": "S"},
            },
            sets_db={"S": {}},
            shapes_db={},
            blueprints_db={},
            role_order=("A", "B"),
            strategy="role_priority",
            module_set_targets={"A": "S", "B": "S"},
            set_effect_modes={},
            core_main_filters={},
            core_set_targets={},
            stat_priority_configs={},
            property_limits={},
        )
        result = {
            "A": {**_plan(drive_a), "assigned_tape": only_core},
            "B": _plan(drive_b),
        }
        pools = {"tapes": {"A": [only_core], "B": [only_core]}}
        kernel = AllocationKernel(type("Scoring", (), {})())

        kernel._annotate_missing_core_reasons(request, pools, result)

        self.assertNotIn("missing_core_reason", result["A"])
        self.assertEqual(
            "满足条件的 1 张唯一卡带已分配给其他角色",
            result["B"]["missing_core_reason"],
        )

    def test_missing_core_diagnostic_names_absent_target_set(self) -> None:
        drive = _drive("drive-a")
        request = AllocationKernelRequest(
            inventory=(drive,),
            roles_db={"A": {"default_set": "Missing"}},
            sets_db={},
            shapes_db={},
            blueprints_db={},
            role_order=("A",),
            strategy="role_priority",
            module_set_targets={"A": "Missing"},
            set_effect_modes={},
            core_main_filters={},
            core_set_targets={},
            stat_priority_configs={},
            property_limits={},
        )
        result = {"A": _plan(drive)}

        AllocationKernel(type("Scoring", (), {})())._annotate_missing_core_reasons(
            request,
            {"tapes": {"A": []}},
            result,
        )

        self.assertEqual(
            "固定快照中没有套装为 Missing 的卡带",
            result["A"]["missing_core_reason"],
        )

    def test_global_optimal_retries_with_full_drive_candidates_after_top_k_failure(self) -> None:
        drives = tuple(_drive(f"drive-{index}") for index in range(3))
        request = AllocationKernelRequest(
            inventory=drives, roles_db={"A": {}}, sets_db={}, shapes_db={},
            blueprints_db={"A": [{"set_pieces": ["H_2"], "extra_pieces": []}]},
            role_order=("A",), strategy="global_optimal", module_set_targets={},
            set_effect_modes={}, core_main_filters={}, core_set_targets={},
            stat_priority_configs={}, property_limits={}, allow_missing_core=True,
            drive_screen_limit=1,
        )
        kernel = AllocationKernel(None)
        calls: list[bool] = []

        def fake_execute_once(_request, _excluded, *, use_full_drive_candidates=False):
            calls.append(use_full_drive_candidates)
            return {"A": _plan(drives[0])} if use_full_drive_candidates else {}

        kernel._execute_once = fake_execute_once  # type: ignore[method-assign]
        result = kernel.execute(request)

        self.assertEqual([False, True], calls)
        self.assertTrue(result["A"]["valid"])

    def test_structural_diagnostic_names_a_missing_shape(self) -> None:
        drive = _drive("only-one")
        request = AllocationKernelRequest(
            inventory=(drive,), roles_db={"A": {}}, sets_db={}, shapes_db={},
            blueprints_db={"A": [{"set_pieces": ["H_2", "H_2"], "extra_pieces": []}]},
            role_order=("A",), strategy="role_priority", module_set_targets={},
            set_effect_modes={}, core_main_filters={}, core_set_targets={},
            stat_priority_configs={}, property_limits={}, allow_missing_core=True,
        )
        kernel = AllocationKernel(None)
        kernel._execute_once = lambda *_args, **_kwargs: {}  # type: ignore[method-assign]

        result = kernel.execute(request)

        self.assertEqual(
            "缺少 H_2 驱动：图纸至少需要 2 个，当前可用 1 个（还缺 1 个）",
            result["A"]["reason"],
        )

    def test_equal_priority_retry_defers_one_role_until_peers_use_their_drives(self) -> None:
        first = _drive("drive-a")
        second = _drive("drive-b")
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "S"}, "B": {"default_set": "S"}},
            {"S": {"shapes": ["H_2"]}},
            {"A": [{"set_pieces": ["H_2"], "extra_pieces": []}], "B": [{"set_pieces": ["H_2"], "extra_pieces": []}]},
        )

        def fake_fit(group, drives_pool, *_args):
            names = tuple(group)
            available = {drive.uid for drive in drives_pool}
            if names == ("A", "B"):
                return {"A": {"valid": False}, "B": {"valid": False}}
            if names == ("A",) and first.uid in available:
                return {"A": _plan(first)}
            if names == ("B",) and second.uid in available:
                return {"B": _plan(second)}
            return {name: {"valid": False} for name in names}

        strategy._find_best_group_fit = fake_fit  # type: ignore[method-assign]
        result = strategy.execute(
            {"drives": [first, second], "tapes": {}},
            ["A", "B"],
            {},
            priority_groups=[["A", "B"]],
        )

        self.assertTrue(result["A"]["valid"])
        self.assertTrue(result["B"]["valid"])
        self.assertEqual({"drive-a", "drive-b"}, strategy._allocated_drive_uids(result))

    def test_equal_priority_recovery_keeps_partial_set_and_extra_drives_frozen(self) -> None:
        frozen_sets = [_drive(f"frozen-set-{index}") for index in range(1, 4)]
        missing_set = _drive("missing-set")
        frozen_extra = _drive("frozen-extra", "H_3")
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "S"}},
            {"S": {"shapes": ["H_2"]}},
            {"A": [{"set_pieces": ["H_2", "H_2", "H_2", "H_2"], "extra_pieces": ["H_3"]}]},
        )
        provisional = {
            "A": {
                "valid": False,
                "blueprint": {
                    "set_pieces": ["H_2", "H_2", "H_2", "H_2"],
                    "extra_pieces": ["H_3"],
                },
                "assigned_tape": None,
                "assigned_set_drives": frozen_sets,
                "assigned_extra_drives": [frozen_extra],
                "score": 2.0,
                "rank_score": 2.0,
            }
        }

        result = strategy._complete_partial_group_fit(
            provisional,
            [*frozen_sets, frozen_extra, missing_set],
            {*(drive.uid for drive in frozen_sets), "frozen-extra", "missing-set"},
            {},
            None,
            15,
        )

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            ["frozen-set-1", "frozen-set-2", "frozen-set-3", "missing-set"],
            [drive.uid for drive in result["A"]["assigned_set_drives"]],
        )
        self.assertEqual(
            ["frozen-extra"],
            [drive.uid for drive in result["A"]["assigned_extra_drives"]],
        )

    def test_failed_strict_role_releases_preallocated_tape_for_next_role(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "S"}, "B": {"default_set": "S"}},
            {"S": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["Y"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
            },
        )
        shared_tape = Tape(
            uid="shared",
            quality="Gold",
            area=15,
            set_name="S",
            main_stats="暴击率%",
            role_scores={"A": 10.0, "B": 10.0},
        )
        drive = _drive("drive-for-b")
        drive.role_scores = {"A": 1.0, "B": 1.0}

        result = strategy.execute(
            {
                "drives": [drive],
                "tapes": {"A": [shared_tape], "B": [shared_tape]},
            },
            ["A", "B"],
            {"A": "S", "B": "S"},
            priority_groups=[["A"], ["B"]],
        )

        self.assertFalse(result["A"]["valid"])
        self.assertTrue(result["B"]["valid"])
        self.assertEqual("shared", result["B"]["assigned_tape"].uid)

    def test_tape_candidate_limit_covers_all_strict_priority_roles(self) -> None:
        role_order = tuple(f"R{index}" for index in range(7))

        _drive_limit, tape_limit = estimate_candidate_pool_limits(
            {},
            role_order,
            tuple((role,) for role in role_order),
        )

        self.assertGreaterEqual(tape_limit, len(role_order))

    def test_later_strict_role_falls_back_to_next_nonempty_tape_layer(self) -> None:
        strategy = RolePriorityStrategy(
            {"A": {"default_set": "S"}, "B": {"default_set": "S"}},
            {"S": {"shapes": []}},
            {
                "A": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
                "B": [{"set_pieces": [], "extra_pieces": ["H_2"]}],
            },
        )
        deeper = Tape(
            uid="abc",
            quality="Gold",
            area=15,
            set_name="S",
            main_stats="暴击率%",
            sub_stats={"a": 1.0, "b": 1.0, "c": 1.0},
            role_scores={"A": 1.0, "B": 1.0},
        )
        fallback = Tape(
            uid="ab",
            quality="Gold",
            area=15,
            set_name="S",
            main_stats="暴击率%",
            sub_stats={"a": 1.0, "b": 1.0},
            role_scores={"A": 100.0, "B": 100.0},
        )
        first_drive = _drive("drive-a")
        second_drive = _drive("drive-b")
        for drive in (first_drive, second_drive):
            drive.role_scores = {"A": 1.0, "B": 1.0}
        priorities = {
            role: {
                "stats": ["a", "b", "c"],
                "ignore_grade_limit": True,
            }
            for role in ("A", "B")
        }

        result = strategy.execute(
            {
                "drives": [first_drive, second_drive],
                "tapes": {
                    "A": [fallback, deeper],
                    "B": [fallback, deeper],
                },
            },
            ["A", "B"],
            {"A": "S", "B": "S"},
            priorities,
            priority_groups=[["A"], ["B"]],
        )

        self.assertEqual("abc", result["A"]["assigned_tape"].uid)
        self.assertEqual("ab", result["B"]["assigned_tape"].uid)


if __name__ == "__main__":
    unittest.main()
