# 验证游戏内装配动作执行器的动作展开、停止和 UI 接入。
"""Tests for executing drive assembly action plans."""

import unittest


class FakeMouseBackend:
    def __init__(self):
        self.calls = []

    def click(self, position):
        self.calls.append(("click", position))

    def drag(self, start, end, duration_ms):
        self.calls.append(("drag", start, end, duration_ms))

    def pause(self, seconds):
        self.calls.append(("pause", round(seconds, 3)))


class DriveAssemblyExecutorTests(unittest.TestCase):
    def test_executes_click_and_drag_actions_in_order(self):
        from src.features.drive_assembly.executor import execute_action_sequence

        backend = FakeMouseBackend()
        report = execute_action_sequence(
            [
                {"name": "filter_button", "position": (10, 20)},
                {"name": "drag_first_tape_to_socket", "from": (30, 40), "to": (50, 60), "duration_ms": 700},
            ],
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual(
            [
                ("click", (10, 20)),
                ("drag", (30, 40), (50, 60), 700),
            ],
            backend.calls,
        )
        self.assertEqual(2, report.executed_actions)
        self.assertEqual([], report.skipped_actions)

    def test_expands_drive_block_placeholders_before_execution(self):
        from src.features.drive_assembly.executor import execute_role_assembly_plan

        backend = FakeMouseBackend()
        plan = {
            "role_name": "真红",
            "available": True,
            "actions": [
                {
                    "name": "install_drives",
                    "sequence": [
                        {"name": "drive_tab", "position": (1, 1)},
                        {"name": "install_drive_block", "block_id": 7, "sequence_index": 0},
                    ],
                    "install_plans": [
                        {
                            "block_id": 7,
                            "install_sequence": [
                                {"name": "shape_select", "position": (2, 2)},
                                {"name": "drag_first_drive_to_block", "from": (3, 3), "to": (4, 4), "duration_ms": 500},
                            ],
                        }
                    ],
                }
            ],
        }

        report = execute_role_assembly_plan(plan, backend=backend, pause_seconds=0.0)

        self.assertEqual(
            [
                ("click", (1, 1)),
                ("click", (2, 2)),
                ("drag", (3, 3), (4, 4), 500),
            ],
            backend.calls,
        )
        self.assertEqual("真红", report.role_name)
        self.assertEqual(3, report.executed_actions)

    def test_stop_checker_prevents_later_actions(self):
        from src.features.drive_assembly.executor import AssemblyExecutionStopped, execute_action_sequence

        backend = FakeMouseBackend()
        checks = iter([False, True])

        with self.assertRaises(AssemblyExecutionStopped):
            execute_action_sequence(
                [
                    {"name": "first", "position": (1, 1)},
                    {"name": "second", "position": (2, 2)},
                ],
                backend=backend,
                pause_seconds=0.0,
                should_stop=lambda: next(checks),
            )

        self.assertEqual([("click", (1, 1))], backend.calls)

    def test_executes_all_ready_role_plans(self):
        from src.features.drive_assembly.executor import execute_all_role_assembly_plan

        backend = FakeMouseBackend()
        plan = {
            "role_plans": [
                {"role_name": "A", "available": True, "actions": [{"name": "install_tape", "sequence": [{"name": "a", "position": (1, 1)}]}]},
                {"role_name": "B", "available": False, "actions": []},
                {"role_name": "C", "available": True, "actions": [{"name": "install_tape", "sequence": [{"name": "c", "position": (3, 3)}]}]},
            ]
        }

        report = execute_all_role_assembly_plan(plan, backend=backend, pause_seconds=0.0)

        self.assertEqual([("click", (1, 1)), ("click", (3, 3))], backend.calls)
        self.assertEqual(["A", "C"], [role.role_name for role in report.role_reports])
        self.assertEqual(2, report.executed_actions)

    def test_executes_role_traversal_and_runs_matching_assembly_plan(self):
        from src.features.drive_assembly.executor import execute_role_traversal_assembly_plan

        backend = FakeMouseBackend()
        traversal_plan = {
            "plans": [
                {
                    "role_name": "A",
                    "action_sequence": [
                        {"name": "role_slot", "position": (100, 100)},
                        {"name": "left_kongmu_tab", "position": (200, 200)},
                        {"name": "assemble_button", "position": (300, 300)},
                        {"name": "run_drive_assembly_for_role", "role_name": "A"},
                    ],
                },
                {
                    "role_name": None,
                    "action_sequence": [
                        {"name": "role_scroll_next_page", "from": (400, 900), "to": (400, 200), "duration_ms": 700}
                    ],
                },
            ]
        }
        assembly_plan = {
            "role_plans": [
                {"role_name": "A", "available": True, "actions": [{"name": "install_tape", "sequence": [{"name": "filter", "position": (10, 10)}]}]},
            ]
        }

        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual(
            [
                ("click", (100, 100)),
                ("click", (200, 200)),
                ("click", (300, 300)),
                ("click", (10, 10)),
                ("drag", (400, 900), (400, 200), 700),
            ],
            backend.calls,
        )
        self.assertEqual(["A"], [role.role_name for role in report.role_reports])
        self.assertEqual(5, report.executed_actions)

    def test_role_traversal_reports_missing_assembly_payload(self):
        from src.features.drive_assembly.executor import execute_role_traversal_assembly_plan

        backend = FakeMouseBackend()
        traversal_plan = {
            "plans": [
                {
                    "role_name": "A",
                    "action_sequence": [
                        {"name": "role_slot", "position": (100, 100)},
                        {"name": "run_drive_assembly_for_role", "role_name": "A"},
                    ],
                }
            ]
        }
        assembly_plan = {"role_plans": []}

        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=backend,
            pause_seconds=0.0,
        )

        self.assertEqual([("click", (100, 100))], backend.calls)
        self.assertEqual(["A"], report.skipped_roles)
        self.assertEqual(1, report.executed_actions)


if __name__ == "__main__":
    unittest.main()
