# 验证清空计算仅丢弃冻结预览，并要求明确确认。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.features.allocation.controller import AllocationController
from src.features.scanning.controller import ScanningController


class AllocationPreviewDiscardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_clear_preview_drops_frozen_result_without_touching_inputs(self) -> None:
        cleared = []
        owner = SimpleNamespace(
            final_plan={"role": {}}, allocation_plan_diff={"role": {}},
            _allocation_dirty=True, _pending_allocation_snapshot_id=7,
            _pending_allocation_static_identity=("dataset",),
            _allocation_lock_snapshot=object(), _selected_locked_role_names=frozenset({"role"}),
            _allocation_custom_weapons={"role": "fork"}, _pending_sel=["role"],
            _equipment_presentation=SimpleNamespace(clear=lambda: cleared.append(True)),
        )
        AllocationController.clear_preview(owner)
        self.assertEqual({}, owner.final_plan)
        self.assertIsNone(owner._pending_allocation_snapshot_id)
        self.assertFalse(owner._allocation_dirty)
        self.assertEqual(["role"], owner._pending_sel)
        self.assertEqual([True], cleared)

    def test_clear_calculation_requires_confirmation_and_only_discards_preview(self) -> None:
        cleared = []
        owner = SimpleNamespace(
            dialog_parent=QWidget(),
            is_running=lambda: False,
            _allocation_controller=SimpleNamespace(clear_preview=lambda: cleared.append(True)),
        )
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            ScanningController.clear_calculation(owner)
        self.assertEqual([], cleared)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            ScanningController.clear_calculation(owner)
        self.assertEqual([True], cleared)
        owner.dialog_parent.deleteLater()

