# 在单次冻结 owner 内复用分析进程；有界帧、取消和失败均回收，不跨账号保留工作进程。
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
from queue import Empty, Queue
import struct
import subprocess
import threading
import time
from uuid import uuid4


class WorkerSessionMixin:
    def _init_worker_session(self):
        self._worker_scope = ContextVar("analysis_worker_scope", default=None)

    def _active_worker(self):
        active = self._worker_scope.get()
        return active if active is not None and active.thread == threading.get_ident() else None

    @contextmanager
    def worker_session(self, *, checkpoint=None):
        """仅显式作用域才复用进程；退出同步回收，旧组件继续一次请求一进程。"""
        if not self.supports_analysis_worker or self._active_worker() is not None:
            yield self
            return
        self._checkpoint(checkpoint)
        worker = _Worker(self)
        token = self._worker_scope.set(worker)
        try:
            yield self
        finally:
            self._worker_scope.reset(token)
            worker.close()


class _Worker:
    def __init__(self, client):
        self.client = client
        self.thread = threading.get_ident()
        self.process = None
        self.session = uuid4().hex
        self.sequence = 0
        self.broken = False
        self.binding = (client.executable, client.dataset_version, client.engine_version, client.component_provenance)

    def close(self):
        process, self.process = self.process, None
        self.broken = True
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        # 取消可能发生在写入中途：子进程已结束并回收，冲刷残留写缓冲会因管道断开失败；
                        # 缓冲流在冲刷失败后仍关闭底层句柄，收尾不因此改写原取消或错误。
                        pass

    @staticmethod
    def _send(pipe, payload):
        pipe.write(struct.pack("<I", len(payload)))
        pipe.write(payload)
        pipe.flush()

    @staticmethod
    def _receive(pipe, maximum):
        def exact(size):
            chunks = bytearray()
            while len(chunks) < size:
                block = pipe.read(min(size - len(chunks), 65536))
                if not block:
                    raise ValueError("truncated_frame")
                chunks.extend(block)
            return bytes(chunks)
        size = struct.unpack("<I", exact(4))[0]
        if not 0 < size <= maximum:
            raise ValueError("frame_limit")
        return exact(size)

    def call(self, payload, checkpoint):
        from src.integrations.analysis_core.nte_analysis_core import MAX_BYTES, NativeAnalysisError, _json_object, RESPONSE_SCHEMA
        client = self.client
        client._checkpoint(checkpoint)
        if (self.broken or len(payload) > MAX_BYTES
                or self.binding != (client.executable, client.dataset_version, client.engine_version, client.component_provenance)):
            raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配")
        client._verify_component(checkpoint)
        initial = self.process is None
        if initial:
            start = time.perf_counter()
            try:
                self.process = subprocess.Popen([str(client.executable), "--worker"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as error:
                self.broken = True
                raise NativeAnalysisError("独立分析核心无法启动") from error
            with client._lock:
                client._stats["process_launches"] += 1
                client._stats["process_launch_seconds"] += time.perf_counter() - start
                client._stats["worker_restarts"] += 1
        process = self.process
        self.sequence += 1
        sequence = self.sequence
        result = Queue(maxsize=1)
        def send_json(value):
            self._send(process.stdin, json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf8"))
        def exchange():
            try:
                if initial:
                    send_json({"kind": "analysis_worker_hello_v1", "session_id": self.session,
                               "dataset_version": client.dataset_version})
                    hello = _json_object(self._receive(process.stdout, 2048))
                    if hello != {"kind": "analysis_worker_ready_v1", "session_id": self.session,
                                 "dataset_version": client.dataset_version, "engine_version": client.engine_version,
                                 "schema_version": RESPONSE_SCHEMA}:
                        raise ValueError("handshake")
                send_json({"kind": "analysis_worker_request_v1", "session_id": self.session, "sequence": sequence})
                self._send(process.stdin, payload)
                header = _json_object(self._receive(process.stdout, 2048))
                if (set(header) != {"kind", "session_id", "sequence", "returncode"}
                        or header["kind"] != "analysis_worker_result_v1" or header["session_id"] != self.session
                        or type(header["sequence"]) is not int or header["sequence"] != sequence
                        or type(header["returncode"]) is not int or header["returncode"] not in (0, 2)):
                    raise ValueError("identity")
                result.put((header["returncode"], self._receive(process.stdout, MAX_BYTES)))
            except Exception as error:
                result.put(error)
        # 写入也在 IO 线程内；stdin 背压不能阻塞 owner 的取消或超时检查。
        io = threading.Thread(target=exchange, name="nte-analysis-worker-io")
        start = time.perf_counter()
        io.start()
        try:
            deadline = time.monotonic() + client.timeout
            while True:
                client._checkpoint(checkpoint)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeAnalysisError("独立分析核心计算超时")
                try:
                    value = result.get(timeout=min(0.1, remaining))
                except Empty:
                    continue
                if isinstance(value, Exception):
                    raise NativeAnalysisError("独立分析核心响应版本或数据集不匹配") from value
                code, raw = value
                client._check_exit(code, raw)
                client._checkpoint(checkpoint)
                with client._lock:
                    client._stats["worker_requests"] += 1
                return raw
        except BaseException:
            # 失败或撤销后不重派当前请求；结束子进程解开两根管道，再 join IO 线程。
            if process.poll() is None:
                process.kill()
            process.wait()
            self.broken = True
            raise
        finally:
            io.join()
            with client._lock:
                client._stats["process_wait_seconds"] += time.perf_counter() - start
