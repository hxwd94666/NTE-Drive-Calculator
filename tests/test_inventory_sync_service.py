# 测试后台背包同步的持续监听、稳定写入和安全停止。
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.inventory_sync_service import InventorySyncService
from src.storage.sqlite.user_data_dao import UserDataDao


def item(
    serial: int,
    *,
    kind: str = "module",
    level: int = 1,
    equipped: bool = False,
    locked: bool = False,
) -> dict:
    return {
        "uid": {"slot": 8, "serial": serial},
        "kind": kind,
        "item_id": f"{kind}-{serial}",
        "suit_id": "suit-1",
        "geometry": "geometry-1",
        "grid": 3,
        "quality": 5,
        "level": level,
        "max_level": 20,
        "locked": locked,
        "equipped": equipped,
        "equipped_character_uid": None,
        "equipped_character_id": None,
        "names": {"zh-Hans": f"驱动 {serial}"},
        "suit_names": {"zh-Hans": "测试套装"},
        "main_stats": [],
        "sub_stats": [],
    }


def snapshot(
    *items: dict,
    generation: int = 1,
    sequence: int = 1,
    characters: list[dict] | None = None,
) -> dict:
    params = {
        "complete": True,
        "item_count": len(items),
        "items": list(items),
        "generation": generation,
        "sequence": sequence,
        "observed_at_unix_ms": 1_800_000_000_000 + sequence,
    }
    if characters is not None:
        params["characters"] = characters
        params["character_count"] = len(characters)
    return {
        "jsonrpc": "2.0",
        "method": "event.inventory.snapshot",
        "params": params,
    }


class FakeCoreClient:
    def __init__(self) -> None:
        self.hello_result = {"protocol_version": 1, "capabilities": ["inventory"]}
        self.handlers: dict[str | None, list] = {}
        self.started = False
        self.closed = False
        self.capture_params: dict | None = None
        self.capture_stopped = False
        self.equipment_params = None
        self._lock = threading.Lock()

    def start(self):
        self.started = True
        return self

    def add_event_handler(self, method, handler) -> None:
        with self._lock:
            self.handlers.setdefault(method, []).append(handler)

    def remove_event_handler(self, method, handler) -> None:
        with self._lock:
            handlers = self.handlers.get(method, [])
            if handler in handlers:
                handlers.remove(handler)

    def start_capture(self, **kwargs):
        self.capture_params = kwargs
        return {"capturing": True}

    def stop_capture(self):
        self.capture_stopped = True
        return {"capturing": False}

    def equip_one_key(self, **kwargs):
        self.equipment_params = kwargs
        return {"status": "dry_run_ok"}

    def close(self) -> None:
        self.closed = True

    def emit(self, event: dict) -> None:
        with self._lock:
            handlers = tuple(self.handlers.get(event.get("method"), ()))
        for handler in handlers:
            handler(event)


class FakeDomainError(RuntimeError):
    domain_code = "NPCAP_NOT_FOUND"


class FailingCaptureCoreClient(FakeCoreClient):
    def start_capture(self, **kwargs):
        raise FakeDomainError("Npcap is unavailable")


class InventorySyncServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "user_data.sqlite3"
        self.core = FakeCoreClient()
        self.template_refreshes: list[bool] = []
        self.service = InventorySyncService(
            self.database_path,
            account_id="tester",
            account_name="测试账号",
            client_factory=lambda: self.core,
            settle_seconds=0.05,
            poll_seconds=0.005,
            template_refresh=lambda: self.template_refreshes.append(True) or {
                "changed": True, "role_count": 1, "fork_count": 1,
            },
        )

    def tearDown(self) -> None:
        if self.service.is_running:
            self.service.stop()
        self.temp_dir.cleanup()

    def _start(self) -> None:
        self.service.start()
        self.service.wait_for_phase("waiting", timeout=2.0)

    def test_starts_capture_without_raw_packet_files(self) -> None:
        self._start()
        self.assertTrue(self.core.started)
        self.assertEqual("inventory", self.core.capture_params["profile"])
        self.assertEqual("disabled", self.core.capture_params["raw_capture"])

    def test_enables_pcapng_capture_only_when_requested(self) -> None:
        core = FakeCoreClient()
        capture_directory = Path(self.temp_dir.name) / "raw_capture"
        service = InventorySyncService(
            self.database_path,
            account_id="tester",
            account_name="测试账号",
            client_factory=lambda: core,
            raw_capture_enabled=True,
            raw_capture_directory=capture_directory,
            poll_seconds=0.005,
        )
        try:
            service.start()
            service.wait_for_phase("waiting", timeout=2.0)
            self.assertEqual("enabled", core.capture_params["raw_capture"])
            self.assertTrue(capture_directory.is_dir())
        finally:
            if service.is_running:
                service.stop()

    def test_reuses_running_core_process_for_one_key_equipment(self) -> None:
        self._start()
        result = self.service.equip_one_key(
            character={"slot": 1, "serial": 2},
            placements=[
                {"equipment": {"slot": 3, "serial": 4}, "row": 2, "column": 3}
            ],
            core={"slot": 5, "serial": 6},
        )

        self.assertEqual(result, {"status": "dry_run_ok"})
        self.assertEqual(self.core.equipment_params["character"]["serial"], 2)

    def test_commits_any_stable_count_and_keeps_listening(self) -> None:
        self._start()
        self.core.emit(snapshot(item(1), sequence=1))
        state = self.service.wait_for_snapshot(timeout=2.0)
        self.assertEqual(1, state.last_item_count)
        self.assertEqual("listening", state.phase)
        self.assertTrue(self.service.is_running)

        with UserDataDao(self.database_path) as dao:
            self.assertEqual(1, dao.current_inventory_summary()["stored_item_count"])
            self.assertEqual(1, dao.summary()["snapshot_count"])
        self.assertEqual([True], self.template_refreshes)

    def test_snapshot_logs_include_authoritative_equipment_and_character_counts(self) -> None:
        events: list[tuple[str, dict[str, object]]] = []
        characters = [{"character_id": 1001, "uid": {"slot": 11, "serial": 22}}]
        with patch(
            "src.services.inventory_sync_service.log_event",
            side_effect=lambda _level, event, _message, _context, **fields: events.append(
                (event, fields)
            ),
        ):
            self._start()
            self.core.emit(
                snapshot(
                    item(1, equipped=True),
                    item(2, kind="core", locked=True),
                    generation=7,
                    sequence=9,
                    characters=characters,
                )
            )
            self.service.wait_for_snapshot(timeout=2.0)

        committed = next(
            fields for event, fields in events if event == "inventory_sync.snapshot_committed"
        )
        candidate = next(
            fields for event, fields in events if event == "inventory_sync.candidate_received"
        )
        self.assertEqual(1, candidate["module_count"])
        self.assertEqual(1, candidate["core_count"])
        self.assertEqual(1, candidate["character_instance_count"])
        self.assertEqual(2, committed["item_count"])
        self.assertEqual(1, committed["module_count"])
        self.assertEqual(1, committed["core_count"])
        self.assertEqual(1, committed["equipped_count"])
        self.assertEqual(1, committed["locked_count"])
        self.assertEqual(1, committed["character_instance_count"])
        self.assertTrue(committed["character_instances_independent"])
        self.assertEqual(7, committed["generation"])
        self.assertEqual(9, committed["sequence"])

    def test_restart_logs_current_snapshot_summary_without_waiting_for_a_change(self) -> None:
        self._start()
        self.core.emit(snapshot(item(1), generation=3, sequence=4, characters=[]))
        self.service.wait_for_snapshot(timeout=2.0)
        self.service.stop()

        events: list[tuple[str, dict[str, object]]] = []
        with patch(
            "src.services.inventory_sync_service.log_event",
            side_effect=lambda _level, event, _message, _context, **fields: events.append(
                (event, fields)
            ),
        ):
            self.service.start()
            self.service.wait_for_phase("listening", timeout=2.0)

        loaded = next(
            fields
            for event, fields in events
            if event == "inventory_sync.current_snapshot_loaded"
        )
        self.assertEqual(1, loaded["item_count"])
        self.assertEqual(1, loaded["module_count"])
        self.assertEqual(0, loaded["core_count"])
        self.assertEqual(0, loaded["character_instance_count"])
        self.assertTrue(loaded["character_instances_independent"])
        self.assertEqual(3, loaded["generation"])
        self.assertEqual(4, loaded["sequence"])

    def test_legacy_snapshot_is_not_presented_as_fast_assembly_ready(self) -> None:
        """A pre-v0.3.5 snapshot has items but no independent character UIDs."""

        self._start()
        self.core.emit(snapshot(item(1), sequence=1))
        first = self.service.wait_for_snapshot(timeout=2.0)
        with UserDataDao(self.database_path) as dao:
            self.assertFalse(
                dao.snapshot_has_independent_character_instances(first.last_snapshot_id)
            )

        self.service.stop()
        self.service.start()
        state = self.service.wait_for_phase("listening", timeout=2.0)
        self.assertIn("旧版背包快照", state.message)

    def test_keeps_new_character_instance_candidate_when_legacy_event_follows(self) -> None:
        """A trailing old-format event must not cancel the v0.3.5 upgrade."""

        self._start()
        self.core.emit(snapshot(item(1), sequence=1))
        first = self.service.wait_for_snapshot(timeout=2.0)
        self.service.stop()

        self.service.start()
        self.service.wait_for_phase("listening", timeout=2.0)
        characters = [{"character_id": 1001, "uid": {"slot": 11, "serial": 22}}]
        self.core.emit(snapshot(item(1), generation=2, sequence=2, characters=characters))
        self.service.wait_for_phase("collecting", timeout=2.0)
        self.core.emit(snapshot(item(1), generation=2, sequence=3))
        second = self.service.wait_for_snapshot(after_snapshot_id=first.last_snapshot_id, timeout=2.0)

        with UserDataDao(self.database_path) as dao:
            self.assertTrue(dao.snapshot_has_independent_character_instances(second.last_snapshot_id))
            self.assertEqual(1001, dao.list_character_instance_mappings()[0]["character_id"])

    def test_two_separate_change_bursts_create_two_atomic_versions(self) -> None:
        self._start()
        self.core.emit(snapshot(item(1), sequence=1))
        first = self.service.wait_for_snapshot(timeout=2.0)

        self.core.emit(snapshot(item(1), item(2), sequence=2))
        second = self.service.wait_for_snapshot(
            after_snapshot_id=first.last_snapshot_id,
            timeout=2.0,
        )
        self.assertGreater(second.last_snapshot_id, first.last_snapshot_id)
        self.assertEqual(2, second.last_item_count)

        with UserDataDao(self.database_path) as dao:
            self.assertEqual(2, dao.summary()["snapshot_count"])
            diff = dao.inventory_snapshot_diff(
                first.last_snapshot_id,
                second.last_snapshot_id,
            )
            self.assertEqual(1, diff["added_count"])
            self.assertEqual(0, diff["removed_count"])

    def test_applies_configured_snapshot_retention_after_sync(self) -> None:
        self._start()
        with UserDataDao(self.database_path) as dao:
            dao.update_sync_settings(inventory_snapshot_retention_count=1)

        self.core.emit(snapshot(item(1), sequence=1))
        first = self.service.wait_for_snapshot(timeout=2.0)
        self.core.emit(snapshot(item(1), item(2), sequence=2))
        second = self.service.wait_for_snapshot(
            after_snapshot_id=first.last_snapshot_id,
            timeout=2.0,
        )

        with UserDataDao(self.database_path) as dao:
            self.assertEqual(1, dao.summary()["snapshot_count"])
            self.assertEqual(second.last_snapshot_id, dao.current_inventory_snapshot_id())

    def test_duplicate_events_do_not_create_duplicate_database_snapshots(self) -> None:
        self._start()
        self.core.emit(snapshot(item(1), sequence=1))
        self.service.wait_for_snapshot(timeout=2.0)
        self.core.emit(snapshot(item(1), sequence=2))
        self.core.emit(snapshot(item(1), sequence=3))
        time.sleep(0.12)

        with UserDataDao(self.database_path) as dao:
            self.assertEqual(1, dao.summary()["snapshot_count"])

    def test_later_removal_and_same_count_edit_are_not_lost(self) -> None:
        self._start()
        self.core.emit(snapshot(item(1), item(2), sequence=1))
        first = self.service.wait_for_snapshot(timeout=2.0)

        self.core.emit(snapshot(item(1, level=2), sequence=2))
        second = self.service.wait_for_snapshot(
            after_snapshot_id=first.last_snapshot_id,
            timeout=2.0,
        )
        self.assertEqual(1, second.last_item_count)
        with UserDataDao(self.database_path) as dao:
            diff = dao.inventory_snapshot_diff(first.last_snapshot_id, second.last_snapshot_id)
            self.assertEqual(1, diff["removed_count"])
            self.assertEqual(1, diff["changed_count"])

    def test_stop_releases_capture_and_core_process(self) -> None:
        self._start()
        self.service.stop()
        self.assertTrue(self.core.capture_stopped)
        self.assertTrue(self.core.closed)
        self.assertEqual("stopped", self.service.state.phase)

    def test_preserves_core_domain_code_for_workbench_guidance(self) -> None:
        failing_core = FailingCaptureCoreClient()
        service = InventorySyncService(
            self.database_path,
            account_id="tester",
            account_name="测试账号",
            client_factory=lambda: failing_core,
            poll_seconds=0.005,
        )

        service.start()
        state = service.wait_for_phase("error", timeout=2.0)

        self.assertEqual("NPCAP_NOT_FOUND", state.error_code)
        self.assertFalse(state.running)


if __name__ == "__main__":
    unittest.main()
