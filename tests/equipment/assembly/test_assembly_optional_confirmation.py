# 可选弹窗不出现是正常检查结果；未知探测、必要动作失败和图纸失败仍阻止完成。
from concurrent.futures import CancelledError
from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from src.features.drive_assembly import executor
from src.features.drive_assembly.assembly_ocr import _maybe_click_optional_confirm
from src.features.drive_assembly.input_backends import AssemblyExecutionReport
from src.features.inventory.equipment.assembly.assembly_dialogs import assembly_report_dialog

NTE_TEST_TIER = "core"


class Backend:
    def __init__(self, image):
        self.image, self.clicks = image, []

    def screenshot(self):
        if isinstance(self.image, Exception):
            raise self.image
        return self.image

    def click(self, point):
        self.clicks.append(point)

    def pause(self, _seconds):
        pass


def action():
    return {"name": "confirm_equipment_reuse_prompt", "optional_confirm_position": (12, 12),
            "modal_probe_position": (50, 50), "brightness_threshold": 150}


def run_sequence(monkeypatch, backend, actions):
    monkeypatch.setattr(executor, "use_ocr_session", lambda **_: nullcontext(SimpleNamespace(check_current=lambda **_: None)))
    return executor.execute_action_sequence(actions, backend=backend, pause_seconds=0, role_name="fixture")


def test_five_absent_optional_prompts_do_not_mark_verified_role_partial(monkeypatch):
    backend = Backend(np.zeros((100, 100, 3), dtype=np.uint8))
    row = run_sequence(monkeypatch, backend, [action() for _ in range(5)])
    assert row.skipped_actions == []
    assert row.executed_actions == 5
    assert backend.clicks == []
    report = AssemblyExecutionReport(role_reports=[row], verified_roles=["fixture"])
    assert assembly_report_dialog("fixture", report, expected_role_count=1)[2]


def test_visible_prompt_is_clicked_once(monkeypatch):
    backend = Backend(np.full((100, 100, 3), 255, dtype=np.uint8))
    row = run_sequence(monkeypatch, backend, [action()])
    assert row.skipped_actions == []
    assert backend.clicks == [(12, 12)]


@pytest.mark.parametrize("image", [None, np.zeros((0, 0, 3), dtype=np.uint8), RuntimeError("capture failed")])
def test_unknown_capture_remains_incomplete(monkeypatch, image):
    backend = Backend(image)
    row = run_sequence(monkeypatch, backend, [action()])
    assert len(row.skipped_actions) == 1
    assert backend.clicks == []
    report = AssemblyExecutionReport(role_reports=[row], verified_roles=["fixture"])
    assert not assembly_report_dialog("fixture", report, expected_role_count=1)[2]


def test_absent_probe_configuration_does_not_count_as_success(monkeypatch):
    item = action()
    item.pop("modal_probe_position")
    row = run_sequence(monkeypatch, Backend(np.zeros((100, 100, 3))), [item])
    assert len(row.skipped_actions) == 1


def test_out_of_bounds_probe_is_unknown(monkeypatch):
    item = {**action(), "modal_probe_position": (500, 500)}
    row = run_sequence(monkeypatch, Backend(np.zeros((100, 100, 3))), [item])
    assert len(row.skipped_actions) == 1


def test_optional_probe_preserves_cancellation():
    with pytest.raises(CancelledError):
        _maybe_click_optional_confirm(action(), Backend(CancelledError()))


def test_required_drive_verification_is_not_bypassed_by_optional_metadata(monkeypatch):
    monkeypatch.setattr(executor, "_retry_missing_drive_block", lambda *_: False)
    item = {**action(), "name": "verify_drive_block_installed"}
    row = run_sequence(monkeypatch, Backend(np.zeros((100, 100, 3))), [item])
    assert len(row.skipped_actions) == 1


def test_required_failure_and_final_verification_are_not_relaxed(monkeypatch):
    row = run_sequence(monkeypatch, Backend(np.zeros((100, 100, 3))), [{"name": "unsupported_required"}])
    assert len(row.skipped_actions) == 1
    report = AssemblyExecutionReport(role_reports=[row], verified_roles=["fixture"])
    assert not assembly_report_dialog("fixture", report, expected_role_count=1)[2]
    row.skipped_actions.clear()
    report.verification_failures.append({"role_name": "fixture", "missing_blocks": [{"block_id": 1}]})
    assert not assembly_report_dialog("fixture", report, expected_role_count=1)[2]
