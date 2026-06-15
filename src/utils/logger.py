# 配置日志输出路径和格式。
"""Shared logging configuration for console and desktop UI output."""

import sys
import os
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

_file_sink_id = logger.add(
    str(LOG_DIR / "nte_runtime.log"),
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
    level="INFO",
    rotation="5 MB",
    retention="7 days",
    encoding="utf-8"
)

def set_log_dir(path: str | Path) -> None:
    global LOG_DIR, _file_sink_id
    new_dir = Path(path)
    new_dir.mkdir(parents=True, exist_ok=True)
    try:
        logger.remove(_file_sink_id)
    except Exception:
        pass
    LOG_DIR = new_dir
    _file_sink_id = logger.add(
        str(LOG_DIR / "nte_runtime.log"),
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} | {message}",
        level="INFO",
        rotation="5 MB",
        retention="7 days",
        encoding="utf-8"
    )

__all__ = ["logger", "set_log_dir", "LOG_DIR"]
