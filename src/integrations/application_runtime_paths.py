# 显式解析发行位置及准备可写目录，普通导入只声明函数。
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


def resolve_application_roots() -> tuple[Path, Path]:
    """区分只读包资源和程序位置；此步骤不探测权限、不创建文件。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS), Path(sys.executable).parent
    root = Path(__file__).resolve().parents[2]
    return root, root


def select_runtime_data_root(
    app_dir: Path,
    *,
    packaged: bool | None = None,
    local_appdata: str | None = None,
) -> Path:
    """先选程序目录；仅发行环境允许回退到本机应用数据目录。"""
    packaged = bool(getattr(sys, "frozen", False)) if packaged is None else packaged
    candidates = [Path(app_dir)]
    if packaged:
        local_appdata = os.environ.get("LOCALAPPDATA") if local_appdata is None else local_appdata
        if local_appdata:
            candidates.append(Path(local_appdata) / "NTE Drive Calc")
    last_error: OSError | None = None
    for base in candidates:
        try:
            for subdir in ("config", "scanned_images", "logs"):
                (base / subdir).mkdir(parents=True, exist_ok=True)
            # 唯一临时探针不覆盖用户文件，关闭时删除；目录可读不等于可写。
            with tempfile.TemporaryFile(dir=base / "config", prefix=".nte-write-", suffix=".tmp") as probe:
                probe.write(b"ok")
                probe.flush()
            return base
        except OSError as error:
            last_error = error
    raise RuntimeError("可写数据目录准备失败，请检查安装目录或用户权限。") from last_error
