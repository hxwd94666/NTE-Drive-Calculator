# 封装 nte-core 的进程生命周期、JSON-RPC 请求和异步事件分发。

from __future__ import annotations

import itertools
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal


PROTOCOL_VERSION = 1
NTE_CORE_ENV = "NTE_CORE_EXE"
_CALLBACK_STOP = object()
_U32_MAX = (1 << 32) - 1
_MAX_EQUIPMENT_PLACEMENTS = 64

JsonObject = dict[str, Any]
EventHandler = Callable[[JsonObject], None]
StderrHandler = Callable[[str], None]


def _queued_event_method(item: object) -> str | None:
    event = item[0] if isinstance(item, tuple) else item
    if not isinstance(event, dict):
        return None
    method = event.get("method")
    return method if isinstance(method, str) else None


class _CoalescingEventQueue:
    """保持可靠事件顺序，同时仅保留最新一条待处理战斗摘要。"""

    def __init__(self) -> None:
        self._items: deque[object] = deque()
        self._condition = threading.Condition()

    def put(self, item: object) -> None:
        with self._condition:
            if _queued_event_method(item) == "event.battle.summary":
                for index in range(len(self._items) - 1, -1, -1):
                    if _queued_event_method(self._items[index]) == "event.battle.summary":
                        del self._items[index]
                        break
            self._items.append(item)
            self._condition.notify()

    def get(self, timeout: float | None = None) -> object:
        if timeout is not None and timeout < 0:
            raise ValueError("timeout must be a non-negative number")
        with self._condition:
            if timeout is None:
                while not self._items:
                    self._condition.wait()
            else:
                deadline = time.monotonic() + timeout
                while not self._items:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise queue.Empty
                    self._condition.wait(remaining)
            return self._items.popleft()

    def get_nowait(self) -> object:
        return self.get(timeout=0.0)


class NteCoreError(RuntimeError):
    """nte-core 集成的基础错误."""


class NteCoreNotFoundError(NteCoreError):
    """当无法解析 nte-core.exe 时引发"""


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
    """当 stdout 违反了已记录的 JSON-RPC/NDJSON 约定或规范时抛出。"""


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


def inventory_item_placement(item: Mapping[str, Any]) -> tuple[int, int] | None:
    """返回驱动块从 1 开始的装备锚点；兼容旧版 core 的缺失字段。"""

    placement = item.get("equipped_placement")
    if placement is None:
        return None
    if not isinstance(placement, Mapping):
        raise NteCoreProtocolError(
            "inventory equipped_placement must be an object or null"
        )
    row = placement.get("row")
    column = placement.get("column")
    if (
        isinstance(row, bool)
        or not isinstance(row, int)
        or isinstance(column, bool)
        or not isinstance(column, int)
        or not 1 <= row <= 5
        or not 1 <= column <= 5
    ):
        raise NteCoreProtocolError(
            "inventory equipped_placement row and column must be integers in 1..5"
        )
    return row, column


def group_inventory_items_by_character(
    snapshot: Mapping[str, Any],
) -> dict[int, list[JsonObject]]:
    """按角色表稳定 ID 分组已解析出装备者的背包条目。

    equipped_character_uid 是账号内实例 UID，不能跨账号或连接关联；这里仅使用
    nte-core 新版提供的 equipped_character_id。旧版 core 缺少该字段时，对应条目
    保持未归属，不做猜测。
    """

    items = snapshot.get("items")
    if not isinstance(items, list):
        raise NteCoreProtocolError("inventory snapshot items must be an array")

    grouped: dict[int, list[JsonObject]] = {}
    for item in items:
        if not isinstance(item, Mapping):
            raise NteCoreProtocolError("inventory snapshot item must be an object")
        inventory_item_placement(item)
        character_id = item.get("equipped_character_id")
        if character_id is None:
            continue
        if isinstance(character_id, bool) or not isinstance(character_id, int) or character_id <= 0:
            raise NteCoreProtocolError(
                "inventory equipped_character_id must be a positive integer or null"
            )
        grouped.setdefault(character_id, []).append(dict(item))
    return grouped


def _equipment_uid(uid: Mapping[str, Any], field: str) -> JsonObject:
    if not isinstance(uid, Mapping):
        raise ValueError(f"{field} must be an item UID object")
    slot = uid.get("slot")
    serial = uid.get("serial")
    for component, value in (("slot", slot), ("serial", serial)):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or value <= 0
            or value >= _U32_MAX
        ):
            raise ValueError(
                f"{field}.{component} must be an integer in 1..4294967294"
            )
    return {"slot": slot, "serial": serial}


def _equipment_grid_position(row: int, column: int) -> tuple[int, int]:
    if (
        isinstance(row, bool)
        or not isinstance(row, int)
        or isinstance(column, bool)
        or not isinstance(column, int)
        or not 1 <= row <= 5
        or not 1 <= column <= 5
    ):
        raise ValueError("row and column must be integers in 1..5")
    return row, column


def _equipment_state(value: bool, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


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
    """按解析优先级返回发布环境和开发环境中的候选路径。"""

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
    """解析显式配置、随包附带、相邻开发目录或 PATH 中的可执行文件。"""

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
    """管理一个 nte-core 进程，并向调用方暴露原始业务 DTO。

    匹配的事件处理器在专用回调线程执行并接管对应事件；未处理事件仍可通过
    get_event 和 drain_events 获取。所属组件退出时，调用方必须关闭客户端。
    """

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
        self._events = _CoalescingEventQueue()
        self._callback_events = _CoalescingEventQueue()
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
                with self._handler_lock:
                    handlers = (
                        *self._handlers.get(method, ()),
                        *self._handlers.get(None, ()),
                    )
                if handlers:
                    self._callback_events.put((message, handlers))
                else:
                    self._events.put(message)
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
            dispatch = self._callback_events.get()
            if dispatch is _CALLBACK_STOP:
                return
            assert isinstance(dispatch, tuple)
            event, handlers = dispatch
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

    def equip_one_key(
        self,
        *,
        character: Mapping[str, Any],
        placements: Sequence[Mapping[str, Any]],
        core: Mapping[str, Any],
        timeout: float | None = None,
    ) -> Any:
        """调用本地装备桥，一次提交角色、驱动位置和核心实例 UID。"""

        return self.call(
            "equipment.equip_one_key",
            {
                "character": dict(character),
                "placements": [dict(placement) for placement in placements],
                "core": dict(core),
            },
            timeout=timeout,
        )

    def add_event_handler(self, method: str | None, handler: EventHandler) -> None:
        """把后续匹配事件分发给后台回调。

        method 传 None 表示匹配所有事件；已由回调接管的事件不再保留给轮询接口。
        """

        with self._handler_lock:
            if handler not in self._handlers[method]:
                self._handlers[method].append(handler)

    def remove_event_handler(self, method: str | None, handler: EventHandler) -> None:
        with self._handler_lock:
            handlers = self._handlers.get(method)
            if handlers and handler in handlers:
                handlers.remove(handler)

    def get_event(self, timeout: float | None = None) -> JsonObject:
        """返回到达时没有匹配回调的下一条事件。"""

        event = self._events.get(timeout=timeout)
        assert isinstance(event, dict)
        return event

    def drain_events(self) -> list[JsonObject]:
        """返回当前队列内所有没有匹配回调的事件。"""

        events: list[JsonObject] = []
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return events
            assert isinstance(event, dict)
            events.append(event)

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

    def get_latest_inventory_by_character(self) -> dict[int, list[JsonObject]]:
        """取得最新背包，并按 characters.json 的稳定角色 ID 分组已装备条目。"""

        return group_inventory_items_by_character(self.get_latest_inventory())

    def _equipment_request(self, method: str, params: JsonObject) -> JsonObject:
        """提交装备插件 RPC；最终状态应以之后的背包快照为准。"""

        return self.call(f"equipment.{method}", params)

    def equip_module(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
        row: int,
        column: int,
    ) -> JsonObject:
        row, column = _equipment_grid_position(row, column)
        return self._equipment_request(
            "equip_module",
            {
                "character": _equipment_uid(character, "character"),
                "equipment": _equipment_uid(equipment, "equipment"),
                "row": row,
                "column": column,
            },
        )

    def equip_core(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> JsonObject:
        return self._equipment_request(
            "equip_core",
            {
                "character": _equipment_uid(character, "character"),
                "equipment": _equipment_uid(equipment, "equipment"),
            },
        )

    def unequip_module(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> JsonObject:
        return self._equipment_request(
            "unequip_module",
            {
                "character": _equipment_uid(character, "character"),
                "equipment": _equipment_uid(equipment, "equipment"),
            },
        )

    def unequip_core(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> JsonObject:
        return self._equipment_request(
            "unequip_core",
            {
                "character": _equipment_uid(character, "character"),
                "equipment": _equipment_uid(equipment, "equipment"),
            },
        )

    def unequip_all(self, *, character: Mapping[str, Any]) -> JsonObject:
        return self._equipment_request(
            "unequip_all",
            {"character": _equipment_uid(character, "character")},
        )

    def equip_one_key(
        self,
        *,
        character: Mapping[str, Any],
        placements: Sequence[Mapping[str, Any]],
        core: Mapping[str, Any],
    ) -> JsonObject:
        if not 1 <= len(placements) <= _MAX_EQUIPMENT_PLACEMENTS:
            raise ValueError("placements must contain 1..64 entries")
        normalized_placements = []
        for index, placement in enumerate(placements):
            if not isinstance(placement, Mapping):
                raise ValueError(f"placements[{index}] must be an object")
            row, column = _equipment_grid_position(
                placement.get("row"), placement.get("column")
            )
            normalized_placements.append(
                {
                    "equipment": _equipment_uid(
                        placement.get("equipment"), f"placements[{index}].equipment"
                    ),
                    "row": row,
                    "column": column,
                }
            )
        return self._equipment_request(
            "equip_one_key",
            {
                "character": _equipment_uid(character, "character"),
                "placements": normalized_placements,
                "core": _equipment_uid(core, "core"),
            },
        )

    def move_module_to_character(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
        row: int,
        column: int,
    ) -> JsonObject:
        row, column = _equipment_grid_position(row, column)
        return self._equipment_request(
            "move_module_to_character",
            {
                "character": _equipment_uid(character, "character"),
                "equipment": _equipment_uid(equipment, "equipment"),
                "row": row,
                "column": column,
            },
        )

    def move_core_to_character(
        self,
        *,
        character: Mapping[str, Any],
        equipment: Mapping[str, Any],
    ) -> JsonObject:
        return self._equipment_request(
            "move_core_to_character",
            {
                "character": _equipment_uid(character, "character"),
                "equipment": _equipment_uid(equipment, "equipment"),
            },
        )

    def set_item_discarded(
        self, *, equipment: Mapping[str, Any], discarded: bool
    ) -> JsonObject:
        return self._equipment_request(
            "set_item_discarded",
            {
                "equipment": _equipment_uid(equipment, "equipment"),
                "discarded": _equipment_state(discarded, "discarded"),
            },
        )

    def set_item_locked(
        self, *, equipment: Mapping[str, Any], locked: bool
    ) -> JsonObject:
        return self._equipment_request(
            "set_item_locked",
            {
                "equipment": _equipment_uid(equipment, "equipment"),
                "locked": _equipment_state(locked, "locked"),
            },
        )

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
