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
                "capabilities": ["capture", "inventory", "battle_summary"],
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
                    },
                    {
                        "uid": {"slot": 5, "serial": 6},
                        "equipped": True,
                        "equipped_character_uid": {"slot": 7, "serial": 8},
                        "equipped_character_id": None,
                    },
                    {
                        "uid": {"slot": 9, "serial": 10},
                        "equipped": False,
                        "equipped_character_uid": None,
                    },
                ],
            },
        })
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
        self.assertEqual(list(grouped), [1020])
        self.assertEqual(grouped[1020][0]["uid"], {"slot": 1, "serial": 2})
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

    def test_inventory_grouping_rejects_invalid_stable_character_id(self):
        with self.assertRaises(NteCoreProtocolError):
            group_inventory_items_by_character(
                {"items": [{"equipped_character_id": "1020"}]}
            )

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
