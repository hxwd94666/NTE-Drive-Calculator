# 将养成体力原生求解器隔离到可重启子进程，避免其异常结束桌面界面。
"""Bounded, reusable process boundary for cultivation stamina calculations."""

from __future__ import annotations

import atexit
import sys
from multiprocessing import get_context
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from threading import Lock

from src.domain.progression_stamina import (
    ProgressionStaminaRequest,
    ProgressionStaminaResult,
)
from src.utils.logger import logger


_TIMEOUT_SECONDS = 15.0
_lock = Lock()
_process: BaseProcess | None = None
_connection: Connection | None = None
_sequence = 0


def calculate_isolated_progression_stamina(
    request: ProgressionStaminaRequest,
) -> ProgressionStaminaResult:
    """Keep SciPy/HiGHS outside the GUI process on Windows.

    A native access violation is not a Python exception. An IPC failure instead
    becomes a bounded calculation error, and the next request starts a new child.
    """

    if sys.platform != "win32":
        from src.services.progression_stamina_service import ProgressionStaminaService

        return ProgressionStaminaService().calculate(request)
    global _sequence
    with _lock:
        connection = _ensure_process()
        _sequence += 1
        serial = _sequence
        try:
            connection.send((serial, request))
            if not connection.poll(_TIMEOUT_SECONDS):
                raise TimeoutError("体力求解超时；求解进程已重置，请重试")
            received_serial, status, payload = connection.recv()
            if received_serial != serial:
                raise OSError("体力求解结果身份不一致")
        except (EOFError, BrokenPipeError, OSError, TimeoutError) as exc:
            logger.warning(
                "cultivation.solver_process_failed | "
                f"reason={type(exc).__name__} "
                f"exit_code={_process.exitcode if _process is not None else 'unknown'}"
            )
            _drop_process()
            raise RuntimeError("体力求解进程异常退出或超时；请重试") from exc
        if status == "value_error":
            raise ValueError(str(payload))
        if status != "ok" or not isinstance(payload, ProgressionStaminaResult):
            raise RuntimeError("体力求解失败；请检查输入后重试")
        return payload


def _ensure_process() -> Connection:
    global _process, _connection
    if _process is not None and _process.is_alive() and _connection is not None:
        return _connection
    _drop_process()
    context = get_context("spawn")
    parent_connection, child_connection = context.Pipe(duplex=True)
    process = context.Process(
        target=_serve,
        args=(child_connection,),
        name="cultivation-stamina-solver",
        daemon=True,
    )
    try:
        process.start()
    except Exception:
        parent_connection.close()
        child_connection.close()
        raise
    child_connection.close()
    _process = process
    _connection = parent_connection
    logger.info("cultivation.solver_process_started | helper_pid={}", process.pid)
    return parent_connection


def _drop_process() -> None:
    global _process, _connection
    connection, process = _connection, _process
    _connection = None
    _process = None
    if connection is not None:
        connection.close()
    if process is not None:
        if process.is_alive():
            process.terminate()
        process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)
        if not process.is_alive():
            process.close()


def _serve(connection: Connection) -> None:
    from src.services.progression_stamina_service import ProgressionStaminaService

    service = ProgressionStaminaService()
    try:
        while True:
            try:
                serial, request = connection.recv()
            except EOFError:
                return
            try:
                result = service.calculate(request)
            except ValueError as exc:
                connection.send((serial, "value_error", str(exc)))
            except Exception:
                connection.send((serial, "error", None))
            else:
                connection.send((serial, "ok", result))
    finally:
        connection.close()


def _shutdown() -> None:
    with _lock:
        _drop_process()


atexit.register(_shutdown)


__all__ = ["calculate_isolated_progression_stamina"]
