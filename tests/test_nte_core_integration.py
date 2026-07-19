# 验证 nte-core 客户端的协议、事件分发和进程生命周期。
import queue
import sys
import threading
import unittest

from src.integrations.nte_core import (
    NteCoreClient,
    NteCoreProcessError,
    NteCoreProtocolError,
    NteCoreRpcError,
    NteCoreTimeoutError,
    group_inventory_items_by_character,
    inventory_item_placement,
)


FAKE_CORE = r'''
import json
import os
import sys
import time


def send(message):
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


deferred = None
for line in sys.stdin:
    request = json.loads(line)
    request_id = request["id"]
    method = request["method"]
    if method == "core.hello":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "core_version": "test",
                "protocol_version": 1,
                "data_version": "test",
                "capabilities": ["capture", "inventory", "battle_summary", "equipment"],
                "raw_capture_default": True,
            },
        })
    elif method == "core.shutdown":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"shutting_down": True}})
        break
    elif method == "core.status":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"core_state": "idle"}})
    elif method == "inventory.get_latest":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "generation": 1,
                "items": [
                    {
                        "uid": {"slot": 1, "serial": 2},
                        "equipped": True,
                        "equipped_character_uid": {"slot": 3, "serial": 4},
                        "equipped_character_id": 1020,
                        "equipped_placement": {"row": 2, "column": 3},
                    },
                    {
                        "uid": {"slot": 5, "serial": 6},
                        "equipped": True,
                        "equipped_character_uid": {"slot": 7, "serial": 8},
                        "equipped_character_id": None,
                        "equipped_placement": None,
                    },
                    {
                        "uid": {"slot": 9, "serial": 10},
                        "equipped": False,
                        "equipped_character_uid": None,
                    },
                ],
            },
        })
    elif method == "equipment.equip_one_key":
        send({"jsonrpc": "2.0", "id": request_id, "result": request["params"]})
    elif method == "test.defer":
        deferred = request_id
        send({"jsonrpc": "2.0", "method": "event.test.deferred", "params": {}})
    elif method == "test.release":
        send({"jsonrpc": "2.0", "id": request_id, "result": "released"})
        send({"jsonrpc": "2.0", "id": deferred, "result": "deferred"})
    elif method == "test.rpc_error":
        send({
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32000,
                "message": "Core error",
                "data": {"domain_code": "NPCAP_NOT_FOUND"},
            },
        })
    elif method == "test.timeout":
        pass
    elif method == "test.events":
        send({"jsonrpc": "2.0", "method": "event.battle.summary", "params": {"sequence": 1}})
        send({"jsonrpc": "2.0", "method": "event.battle.summary", "params": {"sequence": 2}})
        send({"jsonrpc": "2.0", "method": "event.capture.status", "params": {"sequence": 3}})
        send({"jsonrpc": "2.0", "method": "event.battle.summary", "params": {"sequence": 4}})
        send({"jsonrpc": "2.0", "id": request_id, "result": True})
    elif method == "test.callback_burst":
        send({"jsonrpc": "2.0", "method": "event.battle.summary", "params": {"sequence": 1}})
        time.sleep(0.1)
        for sequence in range(2, 101):
            send({"jsonrpc": "2.0", "method": "event.battle.summary", "params": {"sequence": sequence}})
        send({"jsonrpc": "2.0", "method": "event.core.warning", "params": {"sequence": 101}})
        send({"jsonrpc": "2.0", "id": request_id, "result": True})
    elif method == "test.invalid_json":
        sys.stdout.write("not-json\n")
        sys.stdout.flush()
    elif method == "test.exit":
        os._exit(7)
'''

VERSION_MISMATCH_CORE = r'''
import json
import sys


for line in sys.stdin:
    request = json.loads(line)
    result = (
        {"protocol_version": 2}
        if request["method"] == "core.hello"
        else {"shutting_down": True}
    )
    response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
    sys.stdout.write(json.dumps(response) + "\n")
    sys.stdout.flush()
    if request["method"] == "core.shutdown":
        break
'''


def fake_client(script: str = FAKE_CORE, *, timeout: float = 1.0) -> NteCoreClient:
    return NteCoreClient(
        command=[sys.executable, "-u", "-c", script],
        client_version="test",
        timeout=timeout,
    )


class NteCoreClientTests(unittest.TestCase):
    def test_handshake_status_and_shutdown(self):
        client = fake_client().start()

        self.assertEqual(client.hello_result["protocol_version"], 1)
        self.assertEqual(client.status(), {"core_state": "idle"})
        self.assertEqual(client.shutdown(), {"shutting_down": True})
        self.assertFalse(client.is_running)

    def test_one_key_wrapper_uses_protocol_native_uid_payload(self):
        with fake_client() as client:
            result = client.equip_one_key(
                character={"slot": 1, "serial": 2},
                placements=[
                    {
                        "equipment": {"slot": 3, "serial": 4},
                        "row": 2,
                        "column": 3,
                    }
                ],
                core={"slot": 5, "serial": 6},
            )

        self.assertEqual(result["character"], {"slot": 1, "serial": 2})
        self.assertEqual(result["placements"][0]["row"], 2)
        self.assertEqual(result["core"], {"slot": 5, "serial": 6})

    def test_start_rejects_unsupported_negotiated_version(self):
        client = fake_client(VERSION_MISMATCH_CORE)

        with self.assertRaises(NteCoreProtocolError):
            client.start()
        self.assertFalse(client.is_running)

    def test_concurrent_calls_match_out_of_order_responses_by_id(self):
        with fake_client() as client:
            results = {}
            deferred = threading.Thread(
                target=lambda: results.__setitem__("deferred", client.call("test.defer"))
            )
            deferred.start()
            self.assertEqual(client.get_event(timeout=1.0)["method"], "event.test.deferred")

            results["released"] = client.call("test.release")
            deferred.join(timeout=1.0)

        self.assertFalse(deferred.is_alive())
        self.assertEqual(results, {"released": "released", "deferred": "deferred"})

    def test_rpc_error_preserves_domain_code(self):
        with fake_client() as client:
            with self.assertRaises(NteCoreRpcError) as raised:
                client.call("test.rpc_error")

        self.assertEqual(raised.exception.code, -32000)
        self.assertEqual(raised.exception.domain_code, "NPCAP_NOT_FOUND")

    def test_request_timeout_keeps_client_usable(self):
        with fake_client() as client:
            with self.assertRaises(NteCoreTimeoutError):
                client.call("test.timeout", timeout=0.05)
            self.assertEqual(client.status(), {"core_state": "idle"})

    def test_inventory_groups_only_resolved_stable_character_ids(self):
        with fake_client() as client:
            snapshot = client.get_latest_inventory()
            grouped = group_inventory_items_by_character(snapshot)
            grouped_from_client = client.get_latest_inventory_by_character()

        self.assertEqual(snapshot["items"][0]["equipped_character_id"], 1020)
        self.assertEqual(inventory_item_placement(snapshot["items"][0]), (2, 3))
        self.assertEqual(list(grouped), [1020])
        self.assertEqual(grouped[1020][0]["uid"], {"slot": 1, "serial": 2})
        self.assertEqual(
            grouped[1020][0]["equipped_placement"], {"row": 2, "column": 3}
        )
        self.assertEqual(grouped_from_client, grouped)

    def test_inventory_grouping_keeps_old_core_items_unresolved(self):
        snapshot = {
            "items": [
                {
                    "equipped": True,
                    "equipped_character_uid": {"slot": 3, "serial": 4},
                }
            ]
        }

        self.assertEqual(group_inventory_items_by_character(snapshot), {})
        self.assertIsNone(inventory_item_placement(snapshot["items"][0]))

    def test_inventory_grouping_rejects_invalid_stable_character_id(self):
        with self.assertRaises(NteCoreProtocolError):
            group_inventory_items_by_character(
                {"items": [{"equipped_character_id": "1020"}]}
            )

    def test_inventory_placement_rejects_invalid_protocol_values(self):
        invalid = [
            {"equipped_placement": []},
            {"equipped_placement": {"row": True, "column": 2}},
            {"equipped_placement": {"row": 0, "column": 2}},
            {"equipped_placement": {"row": 2, "column": 6}},
        ]

        for item in invalid:
            with self.subTest(item=item), self.assertRaises(NteCoreProtocolError):
                inventory_item_placement(item)

    def test_equipment_methods_map_all_core_operations(self):
        character = {"slot": 1, "serial": 2}
        module = {"slot": 3, "serial": 4}
        core = {"slot": 5, "serial": 6}
        placement = {"equipment": module, "row": 2, "column": 3}

        with fake_client() as client:
            results = [
                client.equip_module(
                    character=character, equipment=module, row=2, column=3
                ),
                client.equip_core(character=character, equipment=core),
                client.unequip_module(character=character, equipment=module),
                client.unequip_core(character=character, equipment=core),
                client.unequip_all(character=character),
                client.equip_one_key(
                    character=character, placements=[placement], core=core
                ),
                client.move_module_to_character(
                    character=character, equipment=module, row=4, column=5
                ),
                client.move_core_to_character(character=character, equipment=core),
                client.set_item_discarded(equipment=module, discarded=True),
                client.set_item_locked(equipment=module, locked=False),
            ]

        self.assertEqual(
            [result["method"] for result in results],
            [
                "equipment.equip_module",
                "equipment.equip_core",
                "equipment.unequip_module",
                "equipment.unequip_core",
                "equipment.unequip_all",
                "equipment.equip_one_key",
                "equipment.move_module_to_character",
                "equipment.move_core_to_character",
                "equipment.set_item_discarded",
                "equipment.set_item_locked",
            ],
        )
        self.assertEqual(
            results[0]["params"],
            {
                "character": character,
                "equipment": module,
                "row": 2,
                "column": 3,
            },
        )
        self.assertEqual(results[5]["params"]["placements"], [placement])
        self.assertTrue(results[8]["params"]["discarded"])
        self.assertFalse(results[9]["params"]["locked"])

    def test_equipment_methods_reject_invalid_client_values(self):
        client = fake_client()
        character = {"slot": 1, "serial": 2}
        equipment = {"slot": 3, "serial": 4}

        with self.assertRaises(ValueError):
            client.equip_module(
                character={"slot": 0, "serial": 2},
                equipment=equipment,
                row=1,
                column=1,
            )
        with self.assertRaises(ValueError):
            client.equip_module(
                character=character, equipment=equipment, row=1, column=6
            )
        with self.assertRaises(ValueError):
            client.equip_one_key(character=character, placements=[], core=equipment)
        with self.assertRaises(ValueError):
            client.set_item_locked(equipment=equipment, locked=1)

    def test_polling_coalesces_battle_summaries_and_preserves_reliable_order(self):
        with fake_client() as client:
            self.assertTrue(client.call("test.events"))
            events = client.drain_events()

        self.assertEqual(
            [(event["method"], event["params"]["sequence"]) for event in events],
            [("event.capture.status", 3), ("event.battle.summary", 4)],
        )

    def test_callbacks_receive_latest_pending_summary_without_polling_duplicates(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()
        sequences = []

        def handler(event):
            sequences.append(event["params"]["sequence"])
            if len(sequences) == 1:
                entered.set()
                release.wait(timeout=1.0)
            else:
                finished.set()

        with fake_client() as client:
            client.add_event_handler("event.battle.summary", handler)
            self.assertTrue(client.call("test.callback_burst"))
            self.assertTrue(entered.wait(timeout=1.0))
            release.set()
            self.assertTrue(finished.wait(timeout=1.0))
            unhandled = client.drain_events()

        self.assertEqual(sequences, [1, 100])
        self.assertEqual([event["method"] for event in unhandled], ["event.core.warning"])

    def test_invalid_stdout_fails_pending_request_and_terminates_process(self):
        with fake_client() as client:
            with self.assertRaises(NteCoreProtocolError):
                client.call("test.invalid_json")

        self.assertFalse(client.is_running)

    def test_unexpected_process_exit_fails_pending_request(self):
        client = fake_client().start()
        try:
            with self.assertRaises(NteCoreProcessError):
                client.call("test.exit")
        finally:
            client.close()

        self.assertFalse(client.is_running)

    def test_get_event_timeout_uses_queue_empty(self):
        with fake_client() as client:
            with self.assertRaises(queue.Empty):
                client.get_event(timeout=0.01)


if __name__ == "__main__":
    unittest.main()
