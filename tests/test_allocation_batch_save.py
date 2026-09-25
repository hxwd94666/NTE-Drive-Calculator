# 验证计算批量保存的借出解除、锁保护、事务回滚与进度响应。
from __future__ import annotations

from concurrent.futures import CancelledError
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from src.services.saved_state_loadout_bridge import SavedStateLoadoutBridge
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from tests.test_role_loadout_slots import inventory_snapshot, assignment


class AllocationBatchSaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dao = UserDataDao(Path(self.temp.name) / "user.sqlite3", account_id="save-test")
        self.addCleanup(self.dao.close)
        snapshot = inventory_snapshot()
        second = deepcopy(snapshot["params"]["items"][0])
        second["uid"] = {"slot": 23, "serial": 12}
        snapshot["params"]["items"].append(second)
        snapshot["params"]["item_count"] = 2
        self.snapshot_id = self.dao.import_inventory_snapshot(snapshot)

    def row(self, character=1004, key="primary", items=None):
        slot_id = self.dao.create_loadout_slot(character, "测试", slot_key=key)
        return dict(slot_id=slot_id, character_id=character, name="计算方案",
                    source_snapshot_id=self.snapshot_id, status="ready", score=25.0,
                    assignments=[assignment()] if items is None else items,
                    payload={"source_role_name": str(character), "assignment_scores": {"nte-module-22-11": 25.0}})

    def owner(self):
        primary = self.dao.save_loadout_plan(
            name="未计算的角色", character_id=1003, assignments=[assignment()],
            source_snapshot_id=self.snapshot_id, is_active=True, status="ready", score=25.0,
            payload={"schema": "allocation-official-snapshot-v1", "source": "allocation", "source_role_name": "早雾", "assignment_scores": {"nte-module-22-11": 25.0}},
        )
        secondary_slot = self.dao.create_loadout_slot(1003, "备用", slot_key="raid")
        secondary = self.dao.save_plan_to_slot(
            secondary_slot, name="备用", assignments=[assignment()], source_snapshot_id=self.snapshot_id,
            payload={"schema": "allocation-official-snapshot-v1", "source": "allocation", "source_role_name": "早雾", "assignment_scores": {"nte-module-22-11": 25.0}},
        )
        return primary, secondary

    def test_primary_target_releases_all_other_role_current_slots(self):
        self._assert_release("primary")

    def test_secondary_target_releases_all_other_role_current_slots(self):
        self._assert_release("raid")

    def _assert_release(self, key):
        historical = self.owner()
        row = self.row(character=1004 if key == "primary" else 1005, key=key)
        self.dao.save_calculated_loadout_plans([row], checkpoint=lambda: None)
        for slot in self.dao.list_loadout_slots(1003):
            plan = slot["current_plan"]
            self.assertEqual("incomplete", plan["status"])
            self.assertEqual(0, plan["assignments"][0]["uid_slot"])
            self.assertEqual(0.0, plan["score"])
        target = self.dao.get_loadout_slot(row["slot_id"])["current_plan"]
        self.assertEqual(22, target["assignments"][0]["uid_slot"])
        self.assertEqual(key == "primary", target["is_active"])
        self.assertTrue(all(self.dao.get_loadout_plan(pid)["assignments"][0]["uid_slot"] == 22
                            for pid in historical))


    def test_locked_owner_rejects_entire_save(self):
        primary, _ = self.owner()
        self.dao.set_allocation_lock(primary, True)
        row = self.row()
        with self.assertRaises(UserDataValidationError):
            self.dao.save_calculated_loadout_plans([row], checkpoint=lambda: None)
        self.assertIsNone(self.dao.get_loadout_slot(row["slot_id"])["current_plan"])
        self.assertTrue(self.dao.get_loadout_plan(primary)["is_active"])

    def test_late_cancel_rolls_back_target_and_both_previous_owners(self):
        before = self.owner()
        row = self.row()

        def cancel_after_write():
            if self.dao.get_loadout_slot(row["slot_id"])["current_plan"] is not None:
                raise CancelledError()

        with self.assertRaises(CancelledError):
            self.dao.save_calculated_loadout_plans([row], checkpoint=cancel_after_write)
        self.assertIsNone(self.dao.get_loadout_slot(row["slot_id"])["current_plan"])
        self.assertEqual(set(before), {slot["current_plan_id"] for slot in self.dao.list_loadout_slots(1003)})

    def test_batch_uses_one_commit_and_preserves_same_role_alternative(self):
        original, _ = self.owner()
        existing = self.dao.list_loadout_slots(1003)
        row = dict(slot_id=existing[1]["slot_id"], character_id=1003, name="另一方案",
                   assignments=[assignment()], source_snapshot_id=self.snapshot_id, status="ready",
                   score=25, payload={})
        other = self.row(1004, items=[{**assignment(), "uid_slot": 23, "uid_serial": 12}])
        statements = []
        self.dao._db().set_trace_callback(statements.append)
        self.dao.save_calculated_loadout_plans([row, other], checkpoint=lambda: None)
        self.dao._db().set_trace_callback(None)
        self.assertEqual(1, sum(sql == "COMMIT" for sql in statements))
        self.assertTrue(self.dao.get_loadout_plan(original)["is_active"])
        self.assertEqual(22, self.dao.get_loadout_plan(original)["assignments"][0]["uid_slot"])

    def test_cross_role_duplicate_input_rejected_without_partial_save(self):
        a, b = self.row(1004), self.row(1005)
        with self.assertRaises(UserDataValidationError):
            self.dao.save_calculated_loadout_plans([a, b], checkpoint=lambda: None)
        self.assertIsNone(self.dao.get_loadout_slot(a["slot_id"])["current_plan"])

    def test_unrelated_visual_snapshot_uid_does_not_release_old_owner(self):
        first = self.dao.import_inventory_snapshot(inventory_snapshot(), source="vision")
        old = self.dao.save_loadout_plan(
            name="旧视觉方案", character_id=1003, assignments=[assignment()],
            source_snapshot_id=first, is_active=True,
        )
        second = self.dao.import_inventory_snapshot(inventory_snapshot(), source="vision")
        row = {**self.row(), "source_snapshot_id": second}
        self.dao.save_calculated_loadout_plans([row], checkpoint=lambda: None)
        self.assertEqual(old, self.dao.list_loadout_slots(1003)[0]["current_plan_id"])

    def test_failed_second_target_rolls_back_released_owner(self):
        original = self.owner()
        a = self.row()
        b = self.row(1005, items=[{**assignment(), "uid_slot": 23, "uid_serial": 12}])
        save = self.dao.save_loadout_plan

        def fail_second(**kwargs):
            if kwargs["character_id"] == 1005:
                raise RuntimeError("injected write failure")
            return save(**kwargs)

        with patch.object(self.dao, "save_loadout_plan", side_effect=fail_second), self.assertRaises(RuntimeError):
            self.dao.save_calculated_loadout_plans([a, b], checkpoint=lambda: None)
        self.assertEqual(set(original), {slot["current_plan_id"] for slot in self.dao.list_loadout_slots(1003)})
        self.assertIsNone(self.dao.get_loadout_slot(a["slot_id"])["current_plan"])

    def test_frozen_bridge_reads_inventory_and_shapes_once(self):
        from tests.test_saved_state_loadout_bridge import _snapshot, _inventory_item
        snapshot_id = self.dao.import_inventory_snapshot(_snapshot([
            _inventory_item(slot=41, serial=410, kind="module", geometry="ZhiJiao2"),
            _inventory_item(slot=51, serial=510, kind="core"),
        ]))
        static_path = Path(__file__).resolve().parents[1] / "data/game_static.sqlite3"
        with StaticGameDataDao(static_path) as static, \
                patch.object(self.dao, "list_inventory_items", wraps=self.dao.list_inventory_items) as inventory, \
                patch.object(static, "list_shapes", wraps=static.list_shapes) as shapes:
            bridge = SavedStateLoadoutBridge(self.dao, static, frozen_snapshot_id=snapshot_id)
            state = {"blueprint_layout": [["XX"] * 5, ["XX", "L_3_TL", "L_3_TL", "XX", "XX"],
                                          ["XX", "L_3_TL", "XX", "XX", "XX"], ["XX"] * 5, ["XX"] * 5],
                     "equipped_drives": [{"uid": "nte-module-41-410", "shape_id": "L_3_TL"}],
                     "equipped_tape": {"uid": "nte-core-51-510"}}
            for character, name in ((1003, "早雾"), (1004, "测试")):
                bridge.prepare_role_plan(role_name=name, role_state=state, character_id=character,
                                         snapshot_id=snapshot_id)
            self.assertEqual(1, inventory.call_count)
            self.assertEqual(1, shapes.call_count)

    def test_save_service_uses_frozen_rows_and_reports_commit_after_persistence(self):
        from src.services.allocation_lock_service import build_allocation_lock_snapshot
        from src.services.allocation_plan_save import save_allocation_plans
        from tests.test_saved_state_loadout_bridge import _snapshot, _inventory_item
        snapshot = self.dao.import_inventory_snapshot(_snapshot([
            _inventory_item(slot=41, serial=410, kind="module", geometry="ZhiJiao2"),
            _inventory_item(slot=51, serial=510, kind="core"),
        ]))
        path = Path(__file__).resolve().parents[1] / "data/game_static.sqlite3"
        with StaticGameDataDao(path) as static:
            dataset = static.summary()["dataset"]["dataset_id"]
        stat = path.stat()
        slot = self.dao.create_loadout_slot(1003, "主力", slot_key="primary")
        row = dict(role_name="早雾", character_id=1003, snapshot_id=snapshot, name="保存测试",
                   score=25, payload={}, slot_id=slot, role_state={
                       "blueprint_layout": [["XX"] * 5, ["XX", "L_3_TL", "L_3_TL", "XX", "XX"],
                                            ["XX", "L_3_TL", "XX", "XX", "XX"], ["XX"] * 5, ["XX"] * 5],
                       "equipped_drives": [{"uid": "nte-module-41-410", "shape_id": "L_3_TL"}],
                       "equipped_tape": {"uid": "nte-core-51-510"},
                   })
        events = []
        saved = save_allocation_plans(
            database_path=Path(self.temp.name) / "user.sqlite3", static_database_path=path,
            static_identity=(path, dataset, (stat.st_size, stat.st_mtime_ns)),
            lock_snapshot=build_allocation_lock_snapshot(self.dao, inventory_snapshot_id=snapshot),
            rows=[row], checkpoint=lambda: None, progress=events.append,
        )
        self.assertEqual(1, saved)
        self.assertTrue(self.dao.get_loadout_slot(slot)["current_plan"]["is_active"])
        self.assertEqual(2, events[-1][1])  # One validation and one committed batch; UI refresh remains.


class AllocationSaveProgressTests(unittest.TestCase):
    def test_save_to_refresh_transition_keeps_same_window_visible(self):
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QApplication
        from src.features.allocation.save_progress import AllocationSaveProgress
        app = QApplication.instance() or QApplication([])

        class VisibilityEvents(QObject):
            def __init__(self):
                super().__init__()
                self.events = []

            def eventFilter(self, obj, event):
                if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
                    self.events.append(event.type())
                return False

        dialog = AllocationSaveProgress()
        events = VisibilityEvents()
        dialog.installEventFilter(events)
        try:
            self.assertEqual(1, dialog.run(lambda report: 1, SimpleNamespace(_save_worker=None)))
            dialog.set_stage(("正在更新计算结果…", 2, 5))
            app.processEvents()
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.label.isVisible())
            self.assertTrue(dialog.progress.isVisible())
            self.assertEqual([QEvent.Type.Show], events.events)
        finally:
            dialog.close()
            dialog.deleteLater()
            app.processEvents()

    def test_progress_does_not_block_gui_events_and_reaps_worker(self):
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from src.features.allocation.save_progress import AllocationSaveProgress
        app = QApplication.instance() or QApplication([])
        dialog = AllocationSaveProgress()
        owner = SimpleNamespace(_save_worker=None)
        ready = threading.Event()
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: (ticks.append(True), ready.set()))
        timer.start(10)

        def task(report):
            self.assertTrue(ready.wait(2))
            report(("已校验 1/1", 1, 5))
            return 1

        try:
            self.assertEqual(1, dialog.run(task, owner))
            self.assertTrue(ticks)
            self.assertIsNone(owner._save_worker)
            self.assertEqual(1, dialog.progress.value())
        finally:
            timer.stop()
            dialog.close()
            dialog.deleteLater()
            app.processEvents()
