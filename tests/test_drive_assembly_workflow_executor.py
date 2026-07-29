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

    def press_gamepad_button(self, button_name):
        self.calls.append(("gamepad", button_name))

    def push_left_joystick(self, x, y):
        self.calls.append(("left_joystick", x, y))

    def pause(self, seconds):
        self.calls.append(("pause", round(seconds, 3)))


class FakeScreenshotMouseBackend(FakeMouseBackend):
    def __init__(self, image):
        super().__init__()
        self._image = image

    def screenshot(self):
        return self._image


class SequenceScreenshotMouseBackend(FakeMouseBackend):
    def __init__(self, images):
        super().__init__()
        self._images = list(images)

    def screenshot(self):
        if len(self._images) > 1:
            return self._images.pop(0)
        return self._images[0]


class FakeOcrEngine:
    def __init__(self, lines):
        self.lines = lines
        self.images = []

    def extract_lines(self, image):
        self.images.append(image)
        return self.lines


class DriveAssemblyWorkflowExecutorTests(unittest.TestCase):
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
            "missing_roles": ["B"],
            "duplicates": [{"role_name": "A", "page_index": 1, "slot_index": 0}],
            "unrecognized": [{"page_index": 0, "slot_index": 4}],
            "plans": [
                {
                    "role_name": "A",
                    "action_sequence": [
                        {"name": "role_slot", "position": (100, 100)},
                        {"name": "left_kongmu_tab", "position": (200, 200)},
                        {"name": "assemble_button", "position": (300, 300)},
                        {"name": "assemble_current_role_from_blueprint", "role_name": "A"},
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
        self.assertEqual(["B"], report.missing_roles)
        self.assertEqual(1, len(report.duplicate_roles))
        self.assertEqual(1, len(report.unrecognized_roles))

    def test_role_traversal_collects_verification_failures(self):
        from src.features.drive_assembly.executor import execute_role_traversal_assembly_plan

        backend = FakeMouseBackend()
        traversal_plan = {
            "plans": [
                {"role_name": "A", "action_sequence": [{"name": "run_drive_assembly_for_role", "role_name": "A"}]}
            ]
        }
        assembly_plan = {
            "role_plans": [
                {"role_name": "A", "available": True, "actions": [{"name": "install_tape", "sequence": []}]},
            ]
        }

        report = execute_role_traversal_assembly_plan(
            traversal_plan,
            assembly_plan,
            backend=backend,
            pause_seconds=0.0,
            role_verifier=lambda role_name, _plan: {"ok": False, "reason": role_name},
        )

        self.assertEqual([{"role_name": "A", "ok": False, "reason": "A"}], report.verification_failures)

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
