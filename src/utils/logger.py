# 配置日志输出路径和格式。
"""Shared logging configuration for console and desktop UI output."""

import sys
import os
from datetime import datetime
from pathlib import Path
from loguru import logger

if getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys._MEIPASS)
    # 日志写到 exe 同级目录，不写入 _MEIPASS 临时目录
    EXE_DIR = Path(sys.executable).parent
else:
    ROOT_DIR = Path(__file__).resolve().parent.parent.parent
    EXE_DIR = ROOT_DIR

# windowed 模式下 stdout/stderr 为 None，重定向到 devnull 防止 print() 崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")

def _select_log_dir() -> Path:
    candidates = [EXE_DIR / "logs"]
    if getattr(sys, 'frozen', False):
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
    level="DEBUG",
    colorize=True
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
        level="INFO",
        encoding="utf-8",
    )
    return sink_id, path


_file_sink_id = _add_runtime_sink(LOG_DIR)
_session_sink_id: int | None = None
_session_log_path: Path | None = None


def enable_session_log() -> Path:
    global _session_sink_id, _session_log_path
    if _session_sink_id is not None and _session_log_path is not None:
        return _session_log_path
    _session_sink_id, _session_log_path = _add_session_sink(LOG_DIR)
    return _session_log_path


def disable_session_log() -> None:
    global _session_sink_id, _session_log_path
    if _session_sink_id is not None:
        logger.remove(_session_sink_id)
    _session_sink_id = None
    _session_log_path = None


def is_session_log_enabled() -> bool:
    return _session_sink_id is not None


def session_log_path() -> Path | None:
    return _session_log_path

def set_log_dir(path: str | Path) -> None:
    global LOG_DIR, _file_sink_id, _session_sink_id, _session_log_path
    new_dir = Path(path)
    new_dir.mkdir(parents=True, exist_ok=True)
    session_was_enabled = is_session_log_enabled()
    if session_was_enabled:
        disable_session_log()
    try:
        logger.remove(_file_sink_id)
    except Exception as exc:
        sys.stderr.write(f"切换日志目录时移除旧日志 sink 失败，继续添加新 sink: {exc}\n")
    LOG_DIR = new_dir
    _file_sink_id = _add_runtime_sink(LOG_DIR)
    if session_was_enabled:
        _session_sink_id, _session_log_path = _add_session_sink(LOG_DIR)

__all__ = [
    "LOG_DIR",
    "disable_session_log",
    "enable_session_log",
    "is_session_log_enabled",
    "logger",
    "session_log_path",
    "set_log_dir",
]
