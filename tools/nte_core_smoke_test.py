# 登录游戏期间验证本地核心组件背包快照输出的开发者工具。
#!/usr/bin/env python3
"""通过 nte-core 手动同步一份 NTE 原始背包快照。

这是开发者冒烟测试，不属于应用的截图扫描功能。它会保留本地组件返回的原始
JSON-RPC 数据，并且不会读写 ``real_inventory.json``。输出目录已被 Git 忽略。
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORE_PATH = PROJECT_ROOT / "nte-core.exe"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "logs" / "nte_core"


class NteCoreClient:
    """用于本地 nte-core 组件的轻量 JSON-RPC over NDJSON 客户端。"""

    def __init__(self, executable: Path, *, timeout: float) -> None:
        self.timeout = timeout
        self.process = subprocess.Popen(
            [str(executable), "serve", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._next_id = 1
        self._pending: dict[str, queue.Queue[dict[str, Any] | BaseException]] = {}
        self._pending_lock = threading.Lock()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.diagnostics: queue.Queue[str] = queue.Queue()
        self._shutdown_requested = False
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._reader.start()
        self._stderr_reader.start()

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
        except BaseException as exc:
            self._fail_pending(exc)
        else:
            self._fail_pending(RuntimeError("nte-core 标准输出已关闭"))

    def _read_stderr(self) -> None:
        assert self.process.stderr is not None
        for line in self.process.stderr:
            text = line.rstrip()
            if text:
                self.diagnostics.put(text)

    def _fail_pending(self, exc: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(exc)
            except queue.Full:
                pass

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.process.poll() is not None:
            raise RuntimeError(f"nte-core 已退出，返回码：{self.process.returncode}")

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
        return response

    def close(self) -> None:
        if self.process.poll() is None and not self._shutdown_requested:
            try:
                self.request("core.shutdown")
                self._shutdown_requested = True
            except (BrokenPipeError, RuntimeError, subprocess.SubprocessError, queue.Empty):
                self.process.terminate()
        try:
            self.process.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=self.timeout)

    def __enter__(self) -> "NteCoreClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def result_or_raise(response: dict[str, Any]) -> dict[str, Any]:
    error = response.get("error")
    if error:
        data = error.get("data") if isinstance(error, dict) else None
        domain_code = data.get("domain_code") if isinstance(data, dict) else None
        raise RuntimeError(f"nte-core 返回错误：{domain_code or error}")
    result = response.get("result")
    if not isinstance(result, dict):
        raise RuntimeError(f"nte-core 响应格式不符合预期：{response}")
    return result


def print_json(label: str, value: Any) -> None:
    print(f"\n## {label}")
    print(json.dumps(value, ensure_ascii=False, indent=2))


def print_new_diagnostics(client: NteCoreClient) -> None:
    while True:
        try:
            print(f"[nte-core] {client.diagnostics.get_nowait()}", file=sys.stderr)
        except queue.Empty:
            return


def snapshot_payload(event: dict[str, Any]) -> dict[str, Any] | None:
    """直接返回通知数据，不转换其结构。"""
    payload = event.get("params")
    return payload if isinstance(payload, dict) else None


def save_raw_snapshot(output_dir: Path, message: dict[str, Any]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"inventory_snapshot_{timestamp}.json"
    path.write_text(json.dumps(message, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def inventory_item_count(payload: dict[str, Any]) -> int:
    """优先读取本地组件报告的数量，缺失时使用数组长度。"""
    reported = payload.get("item_count")
    if isinstance(reported, int) and reported >= 0:
        return reported
    items = payload.get("items")
    return len(items) if isinstance(items, list) else 0


def wait_for_inventory_snapshots(
    client: NteCoreClient,
    *,
    timeout: float,
    expected_min_items: int,
    settle_seconds: float,
) -> tuple[dict[str, Any] | None, int, bool]:
    """登录后保留最大完整快照，直到背包事件稳定。

    nte-core 会发送完整快照，但登录背包数据流目前没有单独的结束事件，因此使用
    一段无新事件的静默期作为完成信号。已知背包大致数量时，调用方还可以要求最小
    数量，避免过早结束。
    """
    deadline = time.monotonic() + timeout
    largest_event: dict[str, Any] | None = None
    largest_count = 0
    last_complete_snapshot_at: float | None = None
    waiting_for_minimum_reported = False

    while time.monotonic() < deadline:
        print_new_diagnostics(client)
        now = time.monotonic()
        if largest_event is not None and last_complete_snapshot_at is not None:
            quiet_seconds = now - last_complete_snapshot_at
            if quiet_seconds >= settle_seconds:
                if largest_count >= expected_min_items:
                    return largest_event, largest_count, True
                if not waiting_for_minimum_reported:
                    print(
                        f"已连续 {settle_seconds:.0f} 秒没有新快照，但当前最大快照仅 "
                        f"{largest_count} 件，仍在等待至少 {expected_min_items} 件。",
                        file=sys.stderr,
                    )
                    waiting_for_minimum_reported = True

        remaining = max(0.1, deadline - time.monotonic())
        try:
            event = client.events.get(timeout=min(0.5, remaining))
        except queue.Empty:
            continue

        method = event.get("method", "event")
        if method == "event.capture.status":
            print_json(method, snapshot_payload(event) or event)
            continue
        if method == "event.core.warning" or method == "event.core.error":
            print_json(method, snapshot_payload(event) or event)
            continue
        if method != "event.inventory.snapshot":
            print_json(method, snapshot_payload(event) or event)
            continue

        payload = snapshot_payload(event)
        if not payload:
            print_json(method, event)
            continue
        if payload.get("complete") is not True:
            print(
                "收到尚未完成的背包快照；继续等待。"
            )
            continue

        count = inventory_item_count(payload)
        last_complete_snapshot_at = time.monotonic()
        waiting_for_minimum_reported = False
        generation = payload.get("generation", "?")
        print(
            f"已收到完整背包快照：{count} 件（generation={generation}）；"
            "继续等待后续快照稳定。"
        )
        if largest_event is None or count >= largest_count:
            largest_event = event
            largest_count = count

    return largest_event, largest_count, False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core", type=Path, default=DEFAULT_CORE_PATH, help="nte-core.exe 路径")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="原始 JSON-RPC 快照的输出目录",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=240.0,
        help="数据同步启动后的最长等待秒数（默认：240）",
    )
    parser.add_argument(
        "--expected-min-items",
        type=int,
        default=0,
        help="不接受低于此数量的快照（默认：0）",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=15.0,
        help="最后一个完整快照后等待的静默秒数（默认：15）",
    )
    parser.add_argument(
        "--raw-capture",
        choices=("disabled", "enabled"),
        default="disabled",
        help="是否允许 nte-core 生成底层诊断文件（默认禁用，请勿提交该文件）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = args.core.resolve()
    if not executable.is_file():
        print(f"找不到 nte-core.exe：{executable}", file=sys.stderr)
        return 2
    if args.expected_min_items < 0:
        print("--expected-min-items 不能小于 0。", file=sys.stderr)
        return 2
    if args.settle_seconds <= 0:
        print("--settle-seconds 必须大于 0。", file=sys.stderr)
        return 2

    print("请先让游戏停留在登录/进入游戏页面。")
    print(f"本地组件：{executable}")
    print(f"原始快照输出目录：{args.output_dir.resolve()}")

    with NteCoreClient(executable, timeout=10.0) as client:
        capture_started = False
        try:
            hello = result_or_raise(
                client.request(
                    "core.hello",
                    {
                        "client_name": "NTE Drive Calculator data sync test",
                        "client_version": "0.1.0",
                        "protocol_min": 1,
                        "protocol_max": 1,
                    },
                )
            )
            print_json("core.hello", hello)

            detected = result_or_raise(client.request("capture.detect"))
            print_json("capture.detect", detected)
            if not detected.get("game_process_detected"):
                print("未检测到游戏进程；请启动游戏并停留在登录页后重试。", file=sys.stderr)
                return 1

            start_response = client.request(
                "capture.start",
                {
                    "profile": "inventory",
                    "device": {"mode": "auto"},
                    "include_incoming": True,
                    "server_damage_calibration": True,
                    "raw_capture": args.raw_capture,
                },
            )
            print_json("capture.start", start_response)
            result_or_raise(start_response)
            capture_started = True

            print(
                "\n背包数据同步已启动。现在请在游戏中点击“进入游戏”。"
                "脚本会收集后续完整快照，直到背包数据稳定。"
            )
            event, item_count, settled = wait_for_inventory_snapshots(
                client,
                timeout=args.timeout,
                expected_min_items=args.expected_min_items,
                settle_seconds=args.settle_seconds,
            )
            if event is None:
                print(f"在 {args.timeout:.0f} 秒内未收到完整背包快照。", file=sys.stderr)
                return 1
            if not settled:
                print(
                    f"在 {args.timeout:.0f} 秒内未等到背包数据稳定；"
                    f"最大完整快照为 {item_count} 件，未保存为最终结果。",
                    file=sys.stderr,
                )
                return 1

            saved_path = save_raw_snapshot(args.output_dir, event)
            print(f"\n已保存稳定的原始 nte-core 快照（{item_count} 件）：{saved_path}")
            return 0
        finally:
            if capture_started and client.process.poll() is None:
                try:
                    print_json("capture.stop", client.request("capture.stop"))
                except (RuntimeError, queue.Empty) as exc:
                    print(f"停止背包数据同步失败：{exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
