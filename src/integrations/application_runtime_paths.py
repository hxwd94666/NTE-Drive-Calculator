# 显式解析发行位置及准备可写目录，普通导入只声明函数。
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

from src.integrations.distribution.metadata import Distribution, DistributionError, load_distribution
from src.integrations.distribution.data_roots import resolve_external_data_root
from src.integrations.distribution.lock import reject_links


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
    distribution: Distribution | None = None,
) -> Path:
    """先选程序目录；仅发行环境允许回退到本机应用数据目录。"""
    distribution = load_distribution(app_dir) if distribution is None else distribution
    if distribution.portable:
        root = (resolve_external_data_root(distribution,
                selection_home=Path(local_appdata) if local_appdata else None)
                if distribution.data_policy == "external" else Path(app_dir) / "userdata")
        if distribution.data_policy == "external" and (root.resolve() == Path(app_dir).resolve()
                                                       or Path(app_dir).resolve() in root.resolve().parents):
            raise DistributionError("已选外部数据根位于程序目录内部；请恢复程序目录外的数据选择记录。")
        reject_links(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=root, prefix=".nte-write-") as probe:
                probe.write(b"ok")
                probe.flush()
        except OSError as error:
            raise DistributionError(f"便携数据目录不可写：{root}；请修复权限，数据位置未切换。") from error
        return root
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
