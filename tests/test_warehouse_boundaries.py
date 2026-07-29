# 验证仓库快照、状态计划、视图和 nte-core 写回之间的边界。
from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.integrations.warehouse_state_writer import (
    WarehouseStateWriteError,
    WarehouseStateWriter,
)
from src.services.warehouse_inventory_service import WarehouseInventoryService


class WarehouseBoundaryTests(unittest.TestCase):
    def test_writer_owns_live_core_preflight_and_rpc_order(self):
        class Sync:
            is_running = True
            state = SimpleNamespace(phase="listening")
            core_hello_result = {"capabilities": ["equipment"]}

            def __init__(self):
                self.calls = []

            def set_item_discarded(self, **kwargs):
                self.calls.append(("discarded", kwargs))

            def set_item_locked(self, **kwargs):
                self.calls.append(("locked", kwargs))

        sync = Sync()
        writer = WarehouseStateWriter(sync)
        writer.ensure_ready()
        writer.apply_one(
            {"discarded": True, "locked": False},
            "locked",
            {"slot": 2, "serial": 3},
        )
        self.assertEqual(
            [
                (
                    "discarded",
                    {
                        "equipment": {"slot": 2, "serial": 3},
                        "discarded": False,
                    },
                ),
                (
                    "locked",
                    {
                        "equipment": {"slot": 2, "serial": 3},
                        "locked": True,
                    },
                ),
            ],
            sync.calls,
        )

    def test_writer_rejects_non_listening_session(self):
        sync = SimpleNamespace(
            is_running=True,
            state=SimpleNamespace(phase="collecting"),
            core_hello_result={"capabilities": ["equipment"]},
        )
        with self.assertRaisesRegex(
            WarehouseStateWriteError,
            "稳定监听",
        ):
            WarehouseStateWriter(sync).ensure_ready()

    def test_writer_rejects_missing_capability_and_unknown_state(self):
        sync = SimpleNamespace(
            is_running=True,
            state=SimpleNamespace(phase="listening"),
            core_hello_result={"capabilities": []},
        )
        writer = WarehouseStateWriter(sync)
        with self.assertRaisesRegex(WarehouseStateWriteError, "equipment"):
            writer.ensure_ready()
        with self.assertRaisesRegex(WarehouseStateWriteError, "未知目标状态"):
            writer.apply_one({}, "invalid", {"slot": 1, "serial": 2})

    def test_writer_clears_normal_state_and_unlocks_before_discarding(self):
        class Sync:
            def __init__(self):
                self.calls = []

            def set_item_discarded(self, **kwargs):
                self.calls.append(("discarded", kwargs))

            def set_item_locked(self, **kwargs):
                self.calls.append(("locked", kwargs))

        sync = Sync()
        writer = WarehouseStateWriter(sync)
        equipment = {"slot": 2, "serial": 3}
        writer.apply_one(
            {"discarded": True, "locked": True},
            "normal",
            equipment,
        )
        writer.apply_one(
            {"discarded": False, "locked": True},
            "discarded",
            equipment,
        )

        self.assertEqual(
            [
                ("discarded", {"equipment": equipment, "discarded": False}),
                ("locked", {"equipment": equipment, "locked": False}),
                ("locked", {"equipment": equipment, "locked": False}),
                ("discarded", {"equipment": equipment, "discarded": True}),
            ],
            sync.calls,
        )

    def test_inventory_service_returns_empty_model_without_creating_database(self):
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "missing.sqlite3"
            snapshot = WarehouseInventoryService(
                database_path
            ).load_current_snapshot()
            self.assertEqual(
                {"snapshot_id": None, "source": "", "rows": []},
                snapshot,
            )
            self.assertFalse(database_path.exists())

    def test_warehouse_view_and_controller_do_not_import_runtime_or_user_dao(self):
        forbidden = {
            "src.app.runtime",
            "src.storage.sqlite.user_data_dao",
        }
        violations = []
        for relative in (
            "src/features/inventory/warehouse.py",
            "src/features/inventory/warehouse_controller.py",
            "src/features/inventory/warehouse_presenter.py",
        ):
            path = Path(relative)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = {alias.name for alias in node.names}
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = {node.module}
                else:
                    continue
                if modules & forbidden:
                    violations.append(relative)
        self.assertEqual([], violations)

    def test_warehouse_page_keeps_card_identification_without_duplicate_page_entry(self):
        controller_source = Path(
            "src/features/inventory/warehouse_controller.py"
        ).read_text(encoding="utf-8")
        warehouse_source = Path("src/features/inventory/warehouse.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("截图/手工鉴定", controller_source)
        self.assertIn("identify_requested", warehouse_source)


if __name__ == "__main__":
    unittest.main()
