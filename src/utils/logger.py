# 配置日志输出路径和格式。
"""控制台日志可在导入时使用；账号文件日志仅由显式生命周期开启。"""

import sys
import os
from datetime import datetime
from pathlib import Path
from typing import Mapping
from loguru import logger


class _NullTextStream:
    """无控制台的窗口执行物使用空文本流，避免原生 nul 设备依赖。"""

    encoding = "utf-8"

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def _install_missing_standard_streams() -> None:
    """仅补充缺失的标准流，不创建文件或探测数据目录。"""
    if sys.stdout is None:
        sys.stdout = _NullTextStream()
    if sys.stderr is None:
        sys.stderr = _NullTextStream()


def _is_test_process() -> bool:
    """识别测试进程，降低控制台噪声并隔离各测试进程的日志路径。"""
    if os.environ.get("NTE_TESTING") == "1":
        return True
    runner = Path(str(sys.argv[0] or "")).stem.lower()
    return "pytest" in runner or "unittest" in runner or "pytest" in sys.modules or "unittest" in sys.modules


def _configure_windows_text_streams() -> None:
    """Windows 控制台使用 UTF-8，编码调整失败仍保留原标准流。"""
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


# 普通导入只建立控制台 sink；本机目录探测和账号文件日志由启动/账号 owner 显式接管。
# 测试进程仍有独立的默认路径，显式开启日志后互不轮转同一文件。
LOG_DIR = EXE_DIR / "build" / "test-logs" / str(os.getpid()) if TEST_PROCESS else EXE_DIR / "logs"

logger.remove()


def _sanitize_local_record(record: dict) -> bool:
    """本机日志清除凭据；故障定位需要的文件路径仍按正式契约保留。"""

    # Import lazily: observability's public package also imports this logger.
    from src.observability.redaction import sanitize_local_log_text

    record["message"] = sanitize_local_log_text(record["message"])
    return True

logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level=CONSOLE_LOG_LEVEL,
    colorize=not TEST_PROCESS,
    filter=_sanitize_local_record,
)

_LOG_FORMAT = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}"


def _literal_sink_path(path: Path) -> str:
    """Loguru 对路径做 format；用户目录的大括号是字面量，不是日期占位符。"""
    return str(path).replace("{", "{{").replace("}", "}}")


def _add_runtime_sink(log_dir: Path) -> int:
    """在已确定的账号日志目录创建有界轮转日志。"""
    return logger.add(
        _literal_sink_path(log_dir / "nte_runtime.log"),
        format=_LOG_FORMAT,
        level="INFO",
        rotation="5 MB",
        retention="7 days",
        encoding="utf-8",
        filter=_sanitize_local_record,
    )


def _next_session_log_path(log_dir: Path) -> Path:
    """每次显式启用均使用独立时间戳；同秒重启以数字后缀避让。"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"nte_runtime_{timestamp}.log"
    suffix = 2
    while path.exists():
        path = log_dir / f"nte_runtime_{timestamp}_{suffix}.log"
        suffix += 1
    return path


def _add_session_sink(log_dir: Path) -> tuple[int, Path]:
    """详细日志有独立文件，不与常驻日志共用开关或轮转状态。"""
    path = _next_session_log_path(log_dir)
    sink_id = logger.add(
        _literal_sink_path(path),
        format=_LOG_FORMAT,
        level="DEBUG",
        encoding="utf-8",
        filter=_sanitize_local_record,
    )
    return sink_id, path


_file_sink_id: int | None = None
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
    """显式启用详细日志；重复启用保持当前会话，不重复注册 sink。"""
    global _session_sink_id, _session_log_path
    if _session_sink_id is not None and _session_log_path is not None:
        return _session_log_path
    if _file_sink_id is None:
        set_log_dir(LOG_DIR, reopen_session=False)
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
    """只关闭详细会话日志，常驻日志及当前目录保持。"""
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
    """账号切换时更换常驻 sink；按调用者决定是否恢复详细日志。"""
    global LOG_DIR, _file_sink_id, _session_sink_id, _session_log_path
    new_dir = Path(path)
    new_dir.mkdir(parents=True, exist_ok=True)
    session_was_enabled = is_session_log_enabled()
    if session_was_enabled:
        disable_session_log(reason="log_directory_changed", context=session_context)
    if _file_sink_id is not None:
        try:
            logger.remove(_file_sink_id)
        except ValueError as exc:
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
