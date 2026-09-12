# 验证模式引导在输入功能产生副作用前返回，环境缺口单独说明。
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from src.features.input_operation_entry import request_input_entry


class InputEntryGuidanceTests(TestCase):
    def denied(self):
        return SimpleNamespace(operation_entry=Mock(return_value=False), operation_unavailable=Mock())

    def test_missing_explicit_callback_does_not_start(self):
        self.assertFalse(request_input_entry(SimpleNamespace(), "interface_input", "扫描"))

    def test_scans_decline_before_dependencies_workers_or_minimization(self):
        from src.features.scanning.workflow import _do_exec, _start_scan, _start_gamepad_scan
        owner = self.denied()
        owner.scan_group = SimpleNamespace(checkedId=lambda: 1)
        _do_exec(owner)
        _start_scan(owner, "auto")
        _start_gamepad_scan(owner, 1, capture_driver="mouse")
        _start_gamepad_scan(owner, 1, capture_driver="gamepad")
        self.assertEqual(4, owner.operation_entry.call_count)
        self.assertFalse(hasattr(owner, "_scan_dependencies"))
        owner.operation_unavailable.assert_not_called()

    def test_assembly_declines_before_plan_reads_or_worker_state(self):
        from src.features.inventory.equipment_automatic_assembly_controller import _start_automatic_equipment_assembly
        from src.features.inventory.equipment_assembly_controller import _start_nte_core_equipment_apply, _preview_nte_core_assemble_role
        owner = self.denied()
        _start_automatic_equipment_assembly(owner, ["test"])
        _start_nte_core_equipment_apply(owner, ["test"])
        _preview_nte_core_assemble_role(owner, "test")
        self.assertEqual(3, owner.operation_entry.call_count)
        self.assertFalse(hasattr(owner, "_equipment_apply_worker"))
        owner.operation_unavailable.assert_not_called()

    def test_allowed_native_mode_reports_missing_connection_without_starting(self):
        from src.features.inventory.equipment_assembly_controller import _start_nte_core_equipment_apply
        owner = SimpleNamespace(operation_entry=Mock(return_value=True), operation_unavailable=Mock())
        _start_nte_core_equipment_apply(owner, ["test"])
        owner.operation_unavailable.assert_called_once()
        self.assertEqual("detection", owner.operation_unavailable.call_args.args[2])
        self.assertFalse(hasattr(owner, "_equipment_apply_worker"))

    def test_rewind_and_capture_identification_decline_before_mutation(self):
        from src.features.toolbox.rewind_execution_ui import RewindExecutionUiMixin
        from src.features.identification.controller import IdentificationController
        owner = self.denied()
        RewindExecutionUiMixin._configure_rewind(owner)
        RewindExecutionUiMixin._start_rewind_execution(owner)
        IdentificationController._start_identify_capture_mode(owner)
        self.assertEqual(3, owner.operation_entry.call_count)
        self.assertFalse(hasattr(owner, "_identify_dependencies"))
