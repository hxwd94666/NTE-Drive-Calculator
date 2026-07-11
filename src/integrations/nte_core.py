# 封装 nte-core 的进程生命周期、JSON-RPC 请求和异步事件分发。
"""Thread-safe client for nte-core JSON-RPC 2.0 over NDJSON stdio."""

from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal


PROTOCOL_VERSION = 1
NTE_CORE_ENV = "NTE_CORE_EXE"
_CALLBACK_STOP = object()

JsonObject = dict[str, Any]
EventHandler = Callable[[JsonObject], None]
StderrHandler = Callable[[str], None]


class NteCoreError(RuntimeError):
    """Base error for the local sidecar integration."""


class NteCoreNotFoundError(NteCoreError):
    """Raised when nte-core.exe cannot be resolved."""


class NteCoreProcessError(NteCoreError):
    def __init__(
        self,
        message: str,
        *,
        return_code: int | None = None,
        stderr_lines: Sequence[str] = (),
    ) -> None:
        super().__init__(message)
        self.return_code = return_code
        self.stderr_lines = tuple(stderr_lines)


class NteCoreProtocolError(NteCoreError):
    """Raised when stdout violates the documented JSON-RPC/NDJSON contract."""


class NteCoreTimeoutError(NteCoreError):
    def __init__(self, method: str, timeout: float) -> None:
        super().__init__(f"nte-core request timed out: {method} ({timeout:.1f}s)")
        self.method = method
        self.timeout = timeout


class NteCoreRpcError(NteCoreError):
    def __init__(self, error: Mapping[str, Any]) -> None:
        self.code = int(error.get("code", -32603))
        self.message = str(error.get("message", "Core error"))
        data = error.get("data")
        self.data = dict(data) if isinstance(data, Mapping) else {}
        domain_code = self.data.get("domain_code")
        self.domain_code = str(domain_code) if domain_code is not None else None
        suffix = f" [{self.domain_code}]" if self.domain_code else ""
        super().__init__(f"nte-core RPC error {self.code}{suffix}: {self.message}")


def _deduplicated_paths(paths: Sequence[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = os.path.normcase(str(path.resolve(strict=False)))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def nte_core_candidates() -> list[Path]:
    """Return runtime and development candidates in resolution priority order."""

    project_root = Path(__file__).resolve().parents[2]
    executable_dir = Path(sys.executable).resolve().parent
    candidates: list[Path] = []

    configured = os.environ.get(NTE_CORE_ENV)
    if configured:
        candidates.append(Path(configured).expanduser())

    candidates.extend(
        [
            executable_dir / "nte-core.exe",
            executable_dir / "nte_core" / "nte-core.exe",
        ]
    )
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.extend(
            [
                Path(frozen_root) / "nte-core.exe",
                Path(frozen_root) / "nte_core" / "nte-core.exe",
            ]
        )
    candidates.extend(
        [
            project_root / "nte-core.exe",
            project_root / "tools" / "nte-core.exe",
            project_root.parent / "target" / "release" / "nte-core.exe",
            project_root.parent / "target" / "debug" / "nte-core.exe",
        ]
    )
    discovered = shutil.which("nte-core.exe") or shutil.which("nte-core")
    if discovered:
        candidates.append(Path(discovered))
    return _deduplicated_paths(candidates)


def resolve_nte_core_executable(executable: str | os.PathLike[str] | None = None) -> Path:
    """Resolve an explicit, bundled, sibling-development, or PATH executable."""

    if executable is not None:
        path = Path(executable).expanduser()
        if path.is_file():
            return path.resolve()
        raise NteCoreNotFoundError(f"nte-core executable does not exist: {path}")

    for candidate in nte_core_candidates():
        if candidate.is_file():
            return candidate.resolve()
    raise NteCoreNotFoundError(
        "nte-core.exe was not found; set NTE_CORE_EXE, place it beside the app, "
        "or build the sibling nte-dps-toolkit release target"
    )


class NteCoreClient:
    """Own one nte-core process and expose raw business DTOs to the caller."""

    def __init__(
        self,
        executable: str | os.PathLike[str] | None = None,
        *,
        command: Sequence[str | os.PathLike[str]] | None = None,
        data_dir: str | os.PathLike[str] | None = None,
        log_level: str | None = None,
        client_name: str = "NTE Drive Calc",
        client_version: str = "development",
        timeout: float = 10.0,
        cwd: str | os.PathLike[str] | None = None,
        stderr_handler: StderrHandler | None = None,
    ) -> None:
        if executable is not None and command is not None:
            raise ValueError("executable and command are mutually exclusive")
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")

        self._explicit_executable = executable
        self._base_command = [str(part) for part in command] if command is not None else None
        if self._base_command is not None and not self._base_command:
            raise ValueError("command must not be empty")
        self.data_dir = Path(data_dir).resolve() if data_dir is not None else None
        self.log_level = log_level
        self.client_name = client_name
        self.client_version = client_version
        self.timeout = timeout
        self.cwd = Path(cwd).resolve() if cwd is not None else None
        self.stderr_handler = stderr_handler

        self._process: subprocess.Popen[str] | None = None
        self._request_ids = itertools.count(1)
        self._id_lock = threading.Lock()
        self._pending: dict[str, queue.Queue[JsonObject | BaseException]] = {}
        self._pending_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._handler_lock = threading.Lock()
        self._handlers: dict[str | None, list[EventHandler]] = defaultdict(list)
        self._events: queue.Queue[JsonObject] = queue.Queue()
        self._callback_events: queue.Queue[JsonObject | object] = queue.Queue()
        self._recent_stderr: deque[str] = deque(maxlen=50)
        self._reader_error: BaseException | None = None
        self._expected_exit = threading.Event()
        self._closed = threading.Event()
        self._threads: list[threading.Thread] = []
        self.hello_result: JsonObject | None = None

    @property
    def process_id(self) -> int | None:
        return self._process.pid if self._process is not None else None

    @property
    def recent_stderr(self) -> tuple[str, ...]:
        return tuple(self._recent_stderr)

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _serve_command(self) -> list[str]:
        base = self._base_command
        if base is None:
            base = [str(resolve_nte_core_executable(self._explicit_executable))]
        command = [*base, "serve", "--stdio"]
        if self.data_dir is not None:
            command.extend(["--data-dir", str(self.data_dir)])
        if self.log_level:
            command.extend(["--log-level", self.log_level])
        return command

    def start(self) -> NteCoreClient:
        if self._process is not None:
            if self.is_running:
                return self
            raise NteCoreProcessError(
                "nte-core client instances cannot be restarted after process exit",
                return_code=self._process.returncode,
                stderr_lines=self._recent_stderr,
            )

        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NO_WINDOW
        try:
            self._process = subprocess.Popen(
                self._serve_command(),
                cwd=str(self.cwd) if self.cwd is not None else None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                creationflags=creation_flags,
            )
        except OSError as exc:
            raise NteCoreProcessError(f"could not start nte-core: {exc}") from exc

        self._threads = [
            threading.Thread(target=self._read_stdout, name="nte-core-stdout", daemon=True),
            threading.Thread(target=self._read_stderr, name="nte-core-stderr", daemon=True),
            threading.Thread(target=self._dispatch_events, name="nte-core-events", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

        try:
            result = self.call(
                "core.hello",
                {
                    "client_name": self.client_name,
                    "client_version": self.client_version,
                    "protocol_min": PROTOCOL_VERSION,
                    "protocol_max": PROTOCOL_VERSION,
                },
            )
            if not isinstance(result, Mapping):
                raise NteCoreProtocolError("core.hello result must be an object")
            negotiated = result.get("protocol_version")
            if negotiated != PROTOCOL_VERSION:
                raise NteCoreProtocolError(
                    f"unsupported negotiated protocol version: {negotiated!r}"
                )
            self.hello_result = dict(result)
            return self
        except BaseException:
            self.close()
            raise

    def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            for line in self._process.stdout:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise NteCoreProtocolError(
                        "nte-core emitted invalid JSON on stdout"
                    ) from exc
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise NteCoreProtocolError("nte-core emitted an invalid JSON-RPC object")
                if "id" in message:
                    request_id = str(message["id"])
                    with self._pending_lock:
                        response_queue = self._pending.get(request_id)
                    if response_queue is not None:
                        try:
                            response_queue.put_nowait(message)
                        except queue.Full:
                            pass
                    continue
                method = message.get("method")
                if not isinstance(method, str) or not method.startswith("event."):
                    raise NteCoreProtocolError("nte-core emitted an invalid event object")
                self._events.put(message)
                with self._handler_lock:
                    has_handlers = bool(self._handlers.get(method) or self._handlers.get(None))
                if has_handlers:
                    self._callback_events.put(message)
        except BaseException as exc:
            self._reader_error = exc
            self._fail_pending(exc)
            if self.is_running:
                self._process.terminate()
        finally:
            if not self._expected_exit.is_set() and self._reader_error is None:
                error = self._process_error("nte-core stdout closed unexpectedly")
                self._reader_error = error
                self._fail_pending(error)

    def _read_stderr(self) -> None:
        assert self._process is not None and self._process.stderr is not None
        for line in self._process.stderr:
            message = line.rstrip("\r\n")
            if not message:
                continue
            self._recent_stderr.append(message)
            if self.stderr_handler is not None:
                try:
                    self.stderr_handler(message)
                except Exception:
                    pass

    def _dispatch_events(self) -> None:
        while True:
            event = self._callback_events.get()
            if event is _CALLBACK_STOP:
                return
            assert isinstance(event, dict)
            method = event["method"]
            with self._handler_lock:
                handlers = [*self._handlers.get(method, ()), *self._handlers.get(None, ())]
            for handler in handlers:
                try:
                    handler(event)
                except Exception as exc:
                    if self.stderr_handler is not None:
                        try:
                            self.stderr_handler(
                                f"nte-core event handler failed: {type(exc).__name__}: {exc}"
                            )
                        except Exception:
                            pass

    def _fail_pending(self, error: BaseException) -> None:
        with self._pending_lock:
            pending = list(self._pending.values())
        for response_queue in pending:
            try:
                response_queue.put_nowait(error)
            except queue.Full:
                pass

    def _process_error(self, message: str) -> NteCoreProcessError:
        return_code = self._process.poll() if self._process is not None else None
        return NteCoreProcessError(
            message,
            return_code=return_code,
            stderr_lines=self._recent_stderr,
        )

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        if not self.is_running:
            if self._reader_error is not None:
                raise self._reader_error
            raise self._process_error("nte-core is not running")

        request_timeout = self.timeout if timeout is None else timeout
        if request_timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        with self._id_lock:
            request_id = f"py-{next(self._request_ids)}"
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": dict(params or {}),
        }
        payload = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        response_queue: queue.Queue[JsonObject | BaseException] = queue.Queue(maxsize=1)
        with self._pending_lock:
            self._pending[request_id] = response_queue
        assert self._process is not None and self._process.stdin is not None
        try:
            with self._write_lock:
                self._process.stdin.write(payload)
                self._process.stdin.flush()
            try:
                response = response_queue.get(timeout=request_timeout)
            except queue.Empty as exc:
                raise NteCoreTimeoutError(method, request_timeout) from exc
        except (BrokenPipeError, OSError) as exc:
            raise self._process_error(f"could not write nte-core request: {exc}") from exc
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

        if isinstance(response, BaseException):
            raise response
        if "error" in response:
            error = response["error"]
            if not isinstance(error, Mapping):
                raise NteCoreProtocolError("JSON-RPC error must be an object")
            raise NteCoreRpcError(error)
        if "result" not in response:
            raise NteCoreProtocolError("JSON-RPC response has neither result nor error")
        return response["result"]

    def add_event_handler(self, method: str | None, handler: EventHandler) -> None:
        """Register for one event method, or pass None for every event."""

        with self._handler_lock:
            if handler not in self._handlers[method]:
                self._handlers[method].append(handler)

    def remove_event_handler(self, method: str | None, handler: EventHandler) -> None:
        with self._handler_lock:
            handlers = self._handlers.get(method)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def get_event(self, timeout: float | None = None) -> JsonObject:
        return self._events.get(timeout=timeout)

    def drain_events(self) -> list[JsonObject]:
        events: list[JsonObject] = []
        while True:
            try:
                events.append(self._events.get_nowait())
            except queue.Empty:
                return events

    def status(self) -> JsonObject:
        return self.call("core.status")

    def detect_capture_environment(self) -> JsonObject:
        return self.call("capture.detect")

    def start_capture(
        self,
        *,
        profile: Literal["inventory", "combat"],
        device_name: str | None = None,
        include_incoming: bool = True,
        server_damage_calibration: bool = True,
        raw_capture: Literal["enabled", "disabled"] = "disabled",
    ) -> JsonObject:
        if profile not in ("inventory", "combat"):
            raise ValueError("profile must be 'inventory' or 'combat'")
        if raw_capture not in ("enabled", "disabled"):
            raise ValueError("raw_capture must be 'enabled' or 'disabled'")
        device: JsonObject = (
            {"mode": "name", "name": device_name}
            if device_name is not None
            else {"mode": "auto"}
        )
        return self.call(
            "capture.start",
            {
                "profile": profile,
                "device": device,
                "include_incoming": include_incoming,
                "server_damage_calibration": server_damage_calibration,
                "raw_capture": raw_capture,
            },
        )

    def stop_capture(self) -> JsonObject:
        return self.call("capture.stop")

    def get_latest_inventory(self) -> JsonObject:
        return self.call("inventory.get_latest")

    def get_battle_summary(self, *, subtract_time_stop: bool = True) -> JsonObject | None:
        return self.call(
            "battle.get_summary",
            {"subtract_time_stop": subtract_time_stop},
        )

    def reset_battle(self) -> JsonObject:
        return self.call("battle.reset")

    def shutdown(self) -> JsonObject:
        if self._process is None or self._closed.is_set():
            return {"shutting_down": True}
        if not self.is_running:
            self._finish_process()
            return {"shutting_down": True}
        self._expected_exit.set()
        result = self.call("core.shutdown")
        self._finish_process()
        return result

    def _finish_process(self) -> None:
        if self._closed.is_set():
            return
        self._expected_exit.set()
        process = self._process
        if process is not None:
            if process.stdin is not None:
                try:
                    process.stdin.close()
                except OSError:
                    pass
            try:
                process.wait(timeout=self.timeout)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        self._callback_events.put(_CALLBACK_STOP)
        self._closed.set()
        current = threading.current_thread()
        for thread in self._threads:
            if thread is not current:
                thread.join(timeout=1.0)
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def close(self) -> None:
        if self._process is None or self._closed.is_set():
            return
        if self.is_running and self._reader_error is None:
            try:
                self.shutdown()
                return
            except NteCoreError:
                self._expected_exit.set()
                self._process.terminate()
        elif self.is_running:
            self._expected_exit.set()
            self._process.terminate()
        self._finish_process()

    def __enter__(self) -> NteCoreClient:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.close()
