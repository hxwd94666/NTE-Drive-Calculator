# 配置日志输出路径和格式。
"""Shared logging configuration for console and desktop UI output."""

import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping
from loguru import logger


class _NullTextStream:
    """Minimal text stream used when a windowed executable has no console."""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _install_missing_standard_streams() -> None:
    """Provide harmless streams without relying on the platform null device."""
    if sys.stdout is None:
        sys.stdout = _NullTextStream()
    if sys.stderr is None:
        sys.stderr = _NullTextStream()


def _is_test_process() -> bool:
    """Detect supported test runners before they start executing test cases."""
    if os.environ.get("NTE_TESTING") == "1":
        return True
    runner = Path(str(sys.argv[0] or "")).stem.lower()
    return "pytest" in runner or "unittest" in runner or "pytest" in sys.modules or "unittest" in sys.modules


def _configure_windows_text_streams() -> None:
    """Keep Chinese log text UTF-8 when Windows launches Python in a legacy code page."""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (OSError, ValueError):
                pass


if getattr(sys, "frozen", False):
    ROOT_DIR = Path(sys._MEIPASS)
    # 日志写到 exe 同级目录，不写入 _MEIPASS 临时目录
    EXE_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent.parent
    EXE_DIR = ROOT_DIR

# windowed 模式下 stdout/stderr 为 None，安装空输出流防止 print() 崩溃。
# 不使用 os.devnull：部分安装环境无法解析 Windows 的 ``nul`` 设备名。
_install_missing_standard_streams()
_configure_windows_text_streams()
TEST_PROCESS = _is_test_process()
CONSOLE_LOG_LEVEL = "WARNING" if TEST_PROCESS else "DEBUG"


def _select_log_dir() -> Path:
    if TEST_PROCESS:
        # Parallel unittest shards must never rotate the same Windows file.
        return EXE_DIR / "build" / "test-logs" / str(os.getpid())
    candidates = [EXE_DIR / "logs"]
    if getattr(sys, "frozen", False):
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "NTE Drive Calc" / "logs")
    for path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            probe = path / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return path
        except Exception:
            continue
    return Path(os.environ.get("TEMP", ".")) / "NTE_Drive_Calc_logs"


LOG_DIR = _select_log_dir()
os.makedirs(LOG_DIR, exist_ok=True)

logger.remove()

logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level=CONSOLE_LOG_LEVEL,
    colorize=not TEST_PROCESS,
)

_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}"


def _add_runtime_sink(log_dir: Path) -> int:
    return logger.add(
        str(log_dir / "nte_runtime.log"),
        format=_LOG_FORMAT,
        level="INFO",
        rotation="5 MB",
        retention="7 days",
        encoding="utf-8",
    )


def _next_session_log_path(log_dir: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"nte_runtime_{timestamp}.log"
    suffix = 2
    while path.exists():
        path = log_dir / f"nte_runtime_{timestamp}_{suffix}.log"
        suffix += 1
    return path


def _add_session_sink(log_dir: Path) -> tuple[int, Path]:
    path = _next_session_log_path(log_dir)
    sink_id = logger.add(
        str(path),
        format=_LOG_FORMAT,
        level="DEBUG",
        encoding="utf-8",
    )
    return sink_id, path


_file_sink_id = _add_runtime_sink(LOG_DIR)
_session_sink_id: int | None = None
_session_log_path: Path | None = None


def _context_text(context: Mapping[str, object] | None) -> str:
    if not context:
        return ""
    return " | " + " ".join(
        f"{key}={value}" for key, value in sorted(context.items()) if value is not None
    )


def enable_session_log(
    *,
    context: Mapping[str, object] | None = None,
) -> Path:
    global _session_sink_id, _session_log_path
    if _session_sink_id is not None and _session_log_path is not None:
        return _session_log_path
    _session_sink_id, _session_log_path = _add_session_sink(LOG_DIR)
    logger.info(
        "logging.session_started | 独立时间戳运行日志已开启"
        f" | file={_session_log_path.name}{_context_text(context)}"
    )
    return _session_log_path


def disable_session_log(
    *,
    reason: str = "disabled",
    context: Mapping[str, object] | None = None,
) -> None:
    global _session_sink_id, _session_log_path
    if _session_sink_id is not None:
        logger.info(
            "logging.session_stopped | 独立时间戳运行日志已关闭"
            f" | reason={reason}{_context_text(context)}"
        )
        logger.remove(_session_sink_id)
    _session_sink_id = None
    _session_log_path = None


def is_session_log_enabled() -> bool:
    return _session_sink_id is not None


def session_log_path() -> Path | None:
    return _session_log_path


def set_log_dir(
    path: str | Path,
    *,
    reopen_session: bool = True,
    session_context: Mapping[str, object] | None = None,
) -> None:
    global LOG_DIR, _file_sink_id, _session_sink_id, _session_log_path
    new_dir = Path(path)
    new_dir.mkdir(parents=True, exist_ok=True)
    session_was_enabled = is_session_log_enabled()
    if session_was_enabled:
        disable_session_log(reason="log_directory_changed", context=session_context)
    try:
        logger.remove(_file_sink_id)
    except Exception as exc:
        sys.stderr.write(f"切换日志目录时移除旧日志 sink 失败，继续添加新 sink: {exc}\n")
    LOG_DIR = new_dir
    _file_sink_id = _add_runtime_sink(LOG_DIR)
    if session_was_enabled and reopen_session:
        _session_sink_id, _session_log_path = _add_session_sink(LOG_DIR)
        logger.info(
            "logging.session_started | 日志目录切换后已创建新的独立时间戳运行日志"
            f" | file={_session_log_path.name}{_context_text(session_context)}"
        )


__all__ = [
    "CONSOLE_LOG_LEVEL",
    "LOG_DIR",
    "TEST_PROCESS",
    "disable_session_log",
    "enable_session_log",
    "is_session_log_enabled",
    "logger",
    "session_log_path",
    "set_log_dir",
]
