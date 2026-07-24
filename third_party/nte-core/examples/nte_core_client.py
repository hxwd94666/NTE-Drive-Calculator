#!/usr/bin/env python3
"""Minimal standard-library client for nte-core JSON-RPC over NDJSON stdio."""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


class NteCoreClient:
    def __init__(self, executable: Path, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self.process = subprocess.Popen(
            [str(executable), "serve", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._next_id = 1
        self._shutdown_requested = False
        self._pending: dict[str, queue.Queue[dict[str, Any] | BaseException]] = {}
        self._pending_lock = threading.Lock()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

    def _read_stdout(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                request_id = message.get("id")
                if request_id is None:
                    self.events.put(message)
                    continue
                with self._pending_lock:
                    response_queue = self._pending.get(str(request_id))
                if response_queue is not None:
                    response_queue.put(message)
        except BaseException as error:
            self._fail_pending(error)
        else:
            self._fail_pending(RuntimeError("nte-core stdout closed"))

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(error)
            except queue.Full:
                pass

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.process.poll() is not None:
            raise RuntimeError(f"nte-core exited with code {self.process.returncode}")

        request_id = str(self._next_id)
        self._next_id += 1
        response_queue: queue.Queue[dict[str, Any] | BaseException] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(request, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
            response = response_queue.get(timeout=self.timeout)
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

        if isinstance(response, BaseException):
            raise response
        if method == "core.shutdown":
            self._shutdown_requested = True
        return response

    def drain_events(self) -> list[dict[str, Any]]:
        events = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                return events

    def close(self) -> None:
        if self.process.poll() is None and not self._shutdown_requested:
            try:
                self.request("core.shutdown")
            except (BrokenPipeError, RuntimeError, subprocess.SubprocessError, queue.Empty):
                self.process.terminate()
        self.process.wait(timeout=self.timeout)

    def __enter__(self) -> NteCoreClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def print_message(label: str, message: dict[str, Any]) -> None:
    print(f"\n## {label}")
    print(json.dumps(message, ensure_ascii=False, indent=2))


def print_events(client: NteCoreClient) -> None:
    for event in client.drain_events():
        print_message(event.get("method", "event"), event)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("executable", type=Path, help="path to nte-core.exe")
    parser.add_argument(
        "--live-seconds",
        type=float,
        default=0.0,
        help="optionally start a live capture for this many seconds",
    )
    parser.add_argument("--profile", choices=("inventory", "combat"), default="combat")
    parser.add_argument(
        "--raw-capture", choices=("enabled", "disabled"), default="disabled"
    )
    args = parser.parse_args()

    with NteCoreClient(args.executable) as client:
        print_message(
            "core.hello",
            client.request(
                "core.hello",
                {
                    "client_name": "Python example",
                    "client_version": "1.0.0",
                    "protocol_min": 1,
                    "protocol_max": 1,
                },
            ),
        )
        print_message("core.status", client.request("core.status"))
        print_message("capture.detect", client.request("capture.detect"))

        if args.live_seconds > 0:
            started = client.request(
                "capture.start",
                {
                    "profile": args.profile,
                    "device": {"mode": "auto"},
                    "include_incoming": True,
                    "server_damage_calibration": True,
                    "raw_capture": args.raw_capture,
                },
            )
            print_message("capture.start", started)
            if "result" in started:
                deadline = time.monotonic() + args.live_seconds
                while time.monotonic() < deadline:
                    time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
                    print_events(client)
                print_message(
                    "inventory.get_latest", client.request("inventory.get_latest")
                )
                print_message(
                    "battle.get_summary",
                    client.request(
                        "battle.get_summary", {"subtract_time_stop": True}
                    ),
                )
                print_message("capture.stop", client.request("capture.stop"))
                print_events(client)

        print_message("core.shutdown", client.request("core.shutdown"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
