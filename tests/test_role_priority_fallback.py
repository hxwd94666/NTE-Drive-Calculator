# 验证缺失卡带与平级角色候选兜底的分配规则。
"""Regression coverage for missing-core and equal-priority drive allocation."""

from __future__ import annotations

import unittest

from src.models.equipment import Drive
from src.optimizer.allocation_kernel import AllocationKernel, AllocationKernelRequest
from src.optimizer.role_priority_strategy import RolePriorityStrategy


def _drive(uid: str) -> Drive:
    return Drive(
        uid=uid,
        quality="Gold",
        area=2,
        shape_id="H_2",
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


class RolePriorityFallbackTests(unittest.TestCase):
    def test_missing_core_is_allowed_only_when_all_drives_fill_the_blueprint(self) -> None:
        drive = _drive("drive-1")
        request = AllocationKernelRequest(
            inventory=(drive,), roles_db={"A": {}}, sets_db={}, shapes_db={},
            blueprints_db={}, role_order=("A",), strategy="role_priority",
            module_set_targets={}, set_effect_modes={}, core_main_filters={},
            core_set_targets={}, stat_priority_configs={}, property_limits={},
            allow_missing_core=True,
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


if __name__ == "__main__":
    unittest.main()
