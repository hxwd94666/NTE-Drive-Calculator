# 有界读取独立分析进程的结果与进度，并在调用线程交付冻结请求的事件。
"""One-request pipe ownership; no progress or stderr text escapes validation."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from queue import Empty, Full, Queue
import re
import subprocess
import threading
import time
from typing import Any, BinaryIO


PROGRESS_PHASES = frozenset({
    "load", "target", "analyze", "buff_remove", "core_baseline",
    "core_candidates", "fork", "panel", "details", "serialize",
})
MAX_PROGRESS_BYTES = 2048
MAX_PROGRESS_COUNT = 2**31 - 1
_CHUNK_BYTES = 8192
_CHECKPOINT_SECONDS = 0.1
_ERROR_LINE = re.compile(rb"nte-analysis-core: [a-z][a-z0-9_]*\Z")


class NativeStreamError(RuntimeError):
    """Fixed, public transport reason without child stderr or request content."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate key")
        value[key] = item
    return value


def decode_progress(line: bytes) -> dict[str, Any] | None:
    if len(line) > MAX_PROGRESS_BYTES:
        raise NativeStreamError("独立分析核心进度事件超过大小限制")
    line = line.rstrip(b"\r")
    if _ERROR_LINE.fullmatch(line):
        return None
    try:
        value = json.loads(line, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError):
        raise NativeStreamError("独立分析核心进度协议无效") from None
    if (not isinstance(value, dict)
            or set(value) != {"kind", "phase", "completed", "total"}
            or value["kind"] != "battle_progress_v1"
            or not isinstance(value["phase"], str)
            or value["phase"] not in PROGRESS_PHASES):
        raise NativeStreamError("独立分析核心进度协议无效")
    completed, total = value["completed"], value["total"]
    if not (completed is None and total is None):
        if (type(completed) is not int or type(total) is not int
                or not 0 <= completed <= total <= MAX_PROGRESS_COUNT):
            raise NativeStreamError("独立分析核心进度计数无效")
    return value


def communicate_progress(
    process: subprocess.Popen[bytes], payload: bytes, *, timeout: float,
    max_output_bytes: int, checkpoint: Callable[[], None],
    progress_callback: Callable[[Mapping[str, Any]], None],
) -> tuple[bytes, int]:
    """Drain both pipes concurrently; only the owner executes user callbacks."""
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    queue: Queue[tuple[str, bytes | None]] = Queue(maxsize=32)
    stopping = threading.Event()

    def publish(kind: str, chunk: bytes | None) -> None:
        while not stopping.is_set():
            try:
                queue.put((kind, chunk), timeout=0.05)
                return
            except Full:
                pass

    def reader(pipe: BinaryIO, kind: str) -> None:
        try:
            while not stopping.is_set():
                # read1 returns available pipe data without waiting to fill 8 KiB.
                chunk = pipe.read1(_CHUNK_BYTES)
                if not chunk:
                    break
                publish(kind, chunk)
        except (OSError, ValueError):
            if not stopping.is_set():
                publish("error", None)
        finally:
            publish(kind, None)

    def writer() -> None:
        try:
            process.stdin.write(payload)
            process.stdin.flush()
        except BrokenPipeError:
            # A rejected request can exit before consuming stdin; stdout owns its error.
            pass
        except (OSError, ValueError):
            if not stopping.is_set():
                publish("error", None)
        finally:
            try:
                process.stdin.close()
            except (OSError, ValueError):
                pass

    workers = [
        threading.Thread(target=reader, args=(process.stdout, "stdout"),
                         name="nte-analysis-stdout"),
        threading.Thread(target=reader, args=(process.stderr, "stderr"),
                         name="nte-analysis-stderr"),
        threading.Thread(target=writer, name="nte-analysis-stdin"),
    ]
    output = bytearray()
    pending = bytearray()
    finished: set[str] = set()
    started: list[threading.Thread] = []
    deadline = time.monotonic() + timeout
    next_checkpoint = 0.0

    def deliver(line: bytes) -> None:
        event = decode_progress(line)
        if event is not None:
            checkpoint()
            progress_callback(event)

    try:
        for worker in workers:
            worker.start()
            started.append(worker)
        while len(finished) != 2 or process.poll() is None:
            now = time.monotonic()
            # Pipe fragmentation is not a request boundary. A large response can
            # contain thousands of tiny reads; keep the same 100 ms cancellation
            # cadence as idle waiting instead of rechecking files for each chunk.
            if now >= next_checkpoint:
                checkpoint()
                next_checkpoint = time.monotonic() + _CHECKPOINT_SECONDS
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeStreamError("独立分析核心计算超时")
            try:
                kind, chunk = queue.get(timeout=min(0.1, remaining))
            except Empty:
                continue
            if kind == "error":
                raise NativeStreamError("独立分析核心管道读取失败")
            if chunk is None:
                finished.add(kind)
                if kind == "stderr" and pending:
                    # NDJSON requires the final newline; do not accept truncated events.
                    if _ERROR_LINE.fullmatch(bytes(pending).rstrip(b"\r")):
                        pending.clear()
                    else:
                        raise NativeStreamError("独立分析核心进度协议无效")
            elif kind == "stdout":
                if len(output) + len(chunk) > max_output_bytes:
                    raise NativeStreamError("分析核心响应超过大小限制")
                output.extend(chunk)
            else:
                pending.extend(chunk)
                while (end := pending.find(b"\n")) >= 0:
                    deliver(bytes(pending[:end]))
                    del pending[:end + 1]
                if len(pending) > MAX_PROGRESS_BYTES:
                    raise NativeStreamError("独立分析核心进度事件超过大小限制")
        checkpoint()
        return bytes(output), process.wait()
    finally:
        stopping.set()
        if process.poll() is None:
            process.kill()
        process.wait()
        for worker in started:
            worker.join()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
