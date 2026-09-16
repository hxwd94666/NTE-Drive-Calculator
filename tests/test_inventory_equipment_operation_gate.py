# 验证持续抓包服务的每种最终装备 RPC 均需原生授权，仓库投影与直接入口同步受限。
import threading
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from src.services.inventory_sync_service import InventorySyncService
from src.services.inventory_capture_wait import InventorySyncCancelled


def deny_native(capability):
    if capability == "native_equipment":
        raise PermissionError("packet only")


OPERATIONS = (
    ("equip_one_key", {"character": {}, "placements": [], "core": {}}),
    ("equip_module", {"character": {}, "equipment": {}, "row": 1, "column": 1}),
    ("unequip_module", {"character": {}, "equipment": {}}),
    ("unequip_core", {"character": {}, "equipment": {}}),
    ("unequip_all", {"character": {}}),
    ("move_module_to_character", {"character": {}, "equipment": {}, "row": 1, "column": 1}),
    ("set_item_discarded", {"equipment": {}, "discarded": True}),
    ("set_item_locked", {"equipment": {}, "locked": True}),
)


class InventoryEquipmentGateTests(TestCase):
    def service(self, guard):
        core = Mock()
        core.hello_result = {"capabilities": ["equipment", "inventory"]}
        service = InventorySyncService.__new__(InventorySyncService)
        service.capture_source = "packet"
        service._operation_guard = guard
        service._stop_requested = threading.Event()
        service._event_ready = threading.Event()
        service._context_is_current = lambda: True
        service._client = core
        service._thread = SimpleNamespace(is_alive=lambda: True)
        return service, core

    def test_requested_stop_denies_all_equipment_rpc_while_owner_thread_is_alive(self):
        service, core = self.service(lambda _: None)
        service.request_stop()
        self.assertTrue(service.is_running)
        for method, kwargs in OPERATIONS:
            with self.subTest(method=method), self.assertRaises(InventorySyncCancelled):
                getattr(service, method)(**kwargs)
            getattr(core, method).assert_not_called()

    def test_stale_account_denies_all_equipment_rpc_with_unchanged_native_permission(self):
        service, core = self.service(lambda _: None)
        service._context_is_current = lambda: False
        self.assertTrue(service.is_running)
        self.assertFalse(service._stop_requested.is_set())
        for method, kwargs in OPERATIONS:
            with self.subTest(method=method), self.assertRaises(InventorySyncCancelled):
                getattr(service, method)(**kwargs)
            getattr(core, method).assert_not_called()

    def test_packet_mode_cannot_use_equipment_rpc_even_with_equipment_capability(self):
        service, core = self.service(deny_native)
        for method, kwargs in OPERATIONS:
            with self.subTest(method=method), self.assertRaises(PermissionError):
                getattr(service, method)(**kwargs)
            getattr(core, method).assert_not_called()

    def test_allowed_native_mode_dispatches_all_equipment_rpc(self):
        guard = Mock()
        service, core = self.service(guard)
        for method, kwargs in OPERATIONS:
            getattr(service, method)(**kwargs)
            getattr(core, method).assert_called_once()
        self.assertEqual([(("native_equipment",),)] * len(OPERATIONS), guard.call_args_list)

    def test_one_key_also_requires_core_equipment_capability(self):
        service, core = self.service(lambda _: None)
        core.hello_result = {"capabilities": ["inventory"]}
        with self.assertRaisesRegex(RuntimeError, "equipment"):
            service.equip_one_key(character={}, placements=[], core={})
        core.equip_one_key.assert_not_called()

    def test_low_mode_warehouse_controls_offer_guidance_without_staging(self):
        from src.features.inventory.warehouse_controller import (
            _on_warehouse_selection_changed, _open_warehouse_state_manager,
            _save_warehouse_state_changes, _set_warehouse_selected_state, _toggle_warehouse_item_state,
        )
        from src.features.inventory.warehouse_progress import set_warehouse_management_busy, update_warehouse_save_state

        window = SimpleNamespace(operation_guard=deny_native, operation_entry=Mock(return_value=False))
        buttons = [Mock() for _ in range(5)]
        for name, button in zip(("warehouse_manage_btn", "warehouse_save_btn", "warehouse_normal_btn", "warehouse_lock_btn", "warehouse_discard_btn"), buttons):
            setattr(window, name, button)
        window.warehouse_view = Mock()
        window.warehouse_view.selectionModel().selectedIndexes.return_value = [SimpleNamespace(data=lambda _: {"state_known": True})]
        _on_warehouse_selection_changed(window)
        set_warehouse_management_busy(window, False)
        update_warehouse_save_state(window)
        for button in buttons:
            button.setEnabled.assert_called_with(True)
        _set_warehouse_selected_state(window, "locked")
        _toggle_warehouse_item_state(window, None, "locked")
        _save_warehouse_state_changes(window)
        _open_warehouse_state_manager(window)
        self.assertFalse(hasattr(window, "_warehouse_pending_state_changes"))
