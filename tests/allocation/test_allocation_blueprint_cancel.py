# 验证分配图纸组合与摆盘在取消时停止且不残留底盘写入。
from __future__ import annotations

from concurrent.futures import CancelledError
import unittest

from src.models.equipment import DriveShape
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.dfs_puzzle import DFSPuzzleSolver
from src.solver.orchestrator import NTEPipelineOrchestrator


NTE_TEST_TIER = "core"


class AllocationBlueprintCancelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.shape = DriveShape(
            shape_id="One", label="1型", matrix=[[1]], area=1,
        )

    def test_combination_enumeration_checks_cancel(self) -> None:
        solver = PuzzleCombinatorics({"One": self.shape})
        with self.assertRaises(CancelledError):
            solver.generate_piece_combinations(
                [], "1型", only_max_extra=False, cancel_check=lambda: True,
            )

    def test_dfs_cancel_restores_board(self) -> None:
        solver = DFSPuzzleSolver({"One": self.shape})
        board = [[0]]
        results = []
        checks = 0

        def cancelled() -> bool:
            nonlocal checks
            checks += 1
            return checks == 2

        with self.assertRaises(CancelledError):
            solver.solve(board, ["One"], results, cancel_check=cancelled)
        self.assertEqual([[0]], board)
        self.assertEqual([], results)

    def test_dfs_first_layout_uses_stable_shape_order(self) -> None:
        shapes = {
            name: DriveShape(
                shape_id=name, label="2型", matrix=[[1, 1]], area=2,
            )
            for name in ("A", "B")
        }
        board = [[0, 0], [0, 0]]
        results = []
        DFSPuzzleSolver(shapes).solve(board, ["B", "A"], results, max_solutions=1)

        self.assertEqual("A", results[0][0][0])
        self.assertEqual("B", results[0][1][0])
        self.assertEqual([[0, 0], [0, 0]], board)

    def test_dfs_does_not_publish_an_unfilled_board(self) -> None:
        board = [[0, 0]]
        results = []

        DFSPuzzleSolver({"One": self.shape}).solve(board, ["One"], results)

        self.assertEqual([], results)
        self.assertEqual([[0, 0]], board)

    def test_orchestrator_checks_before_building_role_blueprints(self) -> None:
        orchestrator = NTEPipelineOrchestrator.from_frozen_inputs(
            roles_db={"A": {
                "default_set": "Set", "extra_shape_label": "1型",
                "board_matrix": [[0]],
            }},
            sets_db={"Set": {"shapes": []}},
            shapes_db={"One": self.shape},
        )
        with self.assertRaises(CancelledError):
            orchestrator.solve_blueprints(["A"], cancel_check=lambda: True)
        self.assertEqual({}, orchestrator._blueprint_cache)


if __name__ == "__main__":
    unittest.main()
