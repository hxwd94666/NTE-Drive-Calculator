# 验证属性上下限约束下配装内核的排除搜索行为。
"""属性上下限触发的排除搜索：结果满足约束、首轮不重复计算、无解时给出诊断。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Mapping

from src.models.equipment import Drive, Tape
from src.optimizer.allocation_kernel import (
    AllocationKernel,
    AllocationKernelRequest,
    AllocationPropertyLimit,
)


def _drive(uid: str, crit_rate: float) -> Drive:
    return Drive(
        uid=uid,
        item_type="drive",
        quality="Gold",
        area=1,
        shape_id=f"shape-{uid}",
        set_name="测试套装",
        main_stats={"攻击力": 10.0, "生命值": 20.0},
        sub_stats={"暴击率": crit_rate},
    )


def _tape() -> Tape:
    return Tape(
        uid="tape-1",
        item_type="tape",
        quality="Gold",
        area=15,
        set_name="测试套装",
        main_stats="暴击率",
        main_value=0.0,
        sub_stats={},
    )


class _RecordingKernel(AllocationKernel):
    """用受控的求解结果替身取代真实评分与分配，只观察搜索行为。"""

    def __init__(self, drives: tuple[Drive, ...], tape: Tape) -> None:
        super().__init__(SimpleNamespace(stat_catalog=SimpleNamespace(tape_main_values={})))
        self._drives = drives
        self._tape = tape
        self.exclusions: list[frozenset[str]] = []

    def _execute_once(  # type: ignore[override]
        self,
        request: AllocationKernelRequest,
        excluded_uids: frozenset[str],
        *,
        use_full_drive_candidates: bool = False,
        reuse_scores: bool = False,
    ) -> dict:
        excluded = frozenset(excluded_uids)
        self.exclusions.append(excluded)
        drives = [drive for drive in self._drives if drive.uid not in excluded]
        return {
            role: {
                "valid": True,
                "score": 10.0,
                "blueprint": {
                    "set_pieces": tuple(drive.shape_id for drive in drives),
                    "extra_pieces": (),
                },
                "assigned_set_drives": list(drives),
                "assigned_extra_drives": [],
                "assigned_tape": self._tape,
            }
            for role in request.role_order
        }


class AllocationKernelPropertyLimitTests(unittest.TestCase):
    def _request(
        self,
        inventory: tuple[Drive | Tape, ...],
        property_limits: Mapping[str, tuple[AllocationPropertyLimit, ...]],
    ) -> AllocationKernelRequest:
        return AllocationKernelRequest(
            inventory=inventory,
            roles_db={"A": {}},
            sets_db={},
            shapes_db={},
            blueprints_db={"A": [{"set_pieces": ("shape-d1", "shape-d2"), "extra_pieces": ()}]},
            role_order=("A",),
            strategy="role_priority",
            module_set_targets={},
            set_effect_modes={},
            core_main_filters={},
            core_set_targets={},
            stat_priority_configs={},
            property_limits=dict(property_limits),
            allow_missing_core=True,
        )

    def test_search_returns_plan_that_satisfies_the_limit(self) -> None:
        high, low = _drive("d1", 10.0), _drive("d2", 1.0)
        tape = _tape()
        kernel = _RecordingKernel((high, low), tape)
        request = self._request(
            (high, low, tape),
            {"A": (AllocationPropertyLimit("暴击率", maximum=5.0),)},
        )

        result = kernel.execute(request)

        self.assertTrue(result["A"]["valid"])
        self.assertEqual(
            ["d2"], [drive.uid for drive in result["A"]["assigned_set_drives"]],
        )

    def test_first_pass_is_not_recomputed_when_search_starts(self) -> None:
        high, low = _drive("d1", 10.0), _drive("d2", 1.0)
        tape = _tape()
        kernel = _RecordingKernel((high, low), tape)
        request = self._request(
            (high, low, tape),
            {"A": (AllocationPropertyLimit("暴击率", maximum=5.0),)},
        )

        kernel.execute(request)

        self.assertEqual(1, kernel.exclusions.count(frozenset()))
        self.assertEqual(len(kernel.exclusions), len(set(kernel.exclusions)))

    def test_without_limits_the_first_pass_is_returned(self) -> None:
        high, low = _drive("d1", 10.0), _drive("d2", 1.0)
        tape = _tape()
        kernel = _RecordingKernel((high, low), tape)
        request = self._request((high, low, tape), {})

        result = kernel.execute(request)

        self.assertEqual([frozenset()], kernel.exclusions)
        self.assertEqual(
            ["d1", "d2"], [drive.uid for drive in result["A"]["assigned_set_drives"]],
        )

    def test_unsatisfiable_limit_reports_reason_on_the_first_pass(self) -> None:
        high, low = _drive("d1", 10.0), _drive("d2", 1.0)
        tape = _tape()
        kernel = _RecordingKernel((high, low), tape)
        request = self._request(
            (high, low, tape),
            {"A": (AllocationPropertyLimit("暴击率", minimum=999.0),)},
        )

        result = kernel.execute(request)

        self.assertFalse(result["A"]["valid"])
        self.assertIn("属性上下限", str(result["A"].get("reason")))
        self.assertLessEqual(len(kernel.exclusions), 256)


if __name__ == "__main__":
    unittest.main()
